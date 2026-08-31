# Interceptors Reference

`HttpServerInterceptor` wraps request handling: logging, timing, security checks, CORS, error
translation. In Kora 2.0 it is **synchronous** and the global registration tag **changed**.

## Contents

- [The contract](#the-contract)
- [The tag that changed — read this first](#the-tag-that-changed--read-this-first)
- [Scopes](#scopes)
- [Execution order](#execution-order)
- [After and error logic](#after-and-error-logic)
- [Enriching the request](#enriching-the-request)
- [Migration from 1.x](#migration-from-1x)
- [Pitfalls](#pitfalls)

---

## The contract

```java
package io.koraframework.http.server.common.interceptor;

public interface HttpServerInterceptor {

    HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception;

    interface InterceptChain {
        HttpServerResponse process(HttpServerRequest request) throws Exception;
    }

    static HttpServerInterceptor noop() { … }
}
```

Three things differ from 1.x: there is **no `Context` parameter**, the return type is a plain
`HttpServerResponse` rather than a `CompletionStage`, and both methods declare `throws Exception`
— so a plain `try/catch` around `chain.process(request)` is the natural way to handle failures.

```java
@Component
public final class LoggingInterceptor implements HttpServerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(LoggingInterceptor.class);

    @Override
    public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
        long started = System.nanoTime();
        HttpServerResponse response = chain.process(request);
        log.info("{} {} -> {} ({} ms)", request.method(), request.path(), response.code(),
                (System.nanoTime() - started) / 1_000_000);
        return response;
    }
}
```

`InterceptChain` is the nested `HttpServerInterceptor.InterceptChain`, referenced unqualified
inside an implementing class. In Kotlin, spell it out in the override signature:
`chain: HttpServerInterceptor.InterceptChain`.

---

## The tag that changed — read this first

A **server-scoped** interceptor (one that runs for every route) must be tagged

```java
@Tag(io.koraframework.http.server.common.HttpServer.class)
```

In Kora 1.x the tag was `@Tag(HttpServerModule.class)`. `HttpServerModule` still exists in 2.0, so
the old annotation **still compiles — with no error and no warning — and the interceptor silently
never runs.**

Why: `HttpServerModule` builds the public router and asks for interceptors by the `HttpServer` tag.

```java
public interface HttpServerModule extends … {

    default HttpServerRouter publicHttpApiRouter(All<HttpServerRequestHandler> handlers,
                                                 @Tag(HttpServer.class) All<HttpServerInterceptor> interceptors,
                                                 HttpServerConfig config) {
        return new HttpServerRouter(handlers, interceptors, config);
    }
}
```

Nothing anywhere requests `@Tag(HttpServerModule.class)`, so a component carrying that tag matches
no dependency claim, is pruned from the graph, and is never even constructed.

In a generated `AppGraph` the difference is visible directly. With three interceptors declared —
one tagged `HttpServer`, one tagged `HttpServerModule`, one untagged — only the first appears:

```java
private final Node<Interceptors.ServerTagged> component38;   // @Tag(HttpServer.class)
// LegacyTagged and Untagged: no nodes generated at all

component38 = graphDraw.addNode(_type_of_component38,
    io.koraframework.http.server.common.HttpServer.class,   // ← the tag
    …, g -> new Interceptors.ServerTagged());

component39 = graphDraw.addNode(_type_of_component39, null, …,
    g -> impl.publicHttpApiRouter(
        All.all(g, node(component35), node(component37)),   // handlers
        All.all(g, node(component38)),                      // interceptors — only the tagged one
        g.get(holder0.component33)));
```

Two consequences worth internalising:

- **An untagged `@Component` interceptor is dropped too.** "Fixing" the migration by deleting the
  tag produces exactly the same silent failure. The tag is required, not optional.
- **`@Tag(HttpServer.class)` interceptors never run on the system server.** The two servers are
  built by two `@FactoryModule` instances, and `HttpServerFactoryModule.router` asks for
  `@Tag(Tag.Factory.class) All<HttpServerInterceptor>`. Inside a factory module `@Tag.Factory`
  resolves to *that module's own tag* (`ComponentDeclaration.fromModule`), and
  `UndertowSystemHttpServerModule.undertowSystemHttpApi()` is declared `@FactoryModule @SystemApi`.
  So the system router collects **`@Tag(SystemApi.class)`** interceptors — a different tag, which is
  why `/system/readiness`, `/system/liveness` and `/metrics` see none of yours. Nothing in the
  framework or in the migrated examples registers a system-scoped interceptor; do not use that tag
  to bolt auth onto the probe port — restrict it with a network policy instead.

Because this failure is invisible at build time, cover it with a test that asserts the
interceptor's observable effect (a header, a log line, a counter) on a real request.

---

## Scopes

| Scope | How to apply | Applies to |
|---|---|---|
| **Server** | `@Tag(HttpServer.class)` + `@Component` on the interceptor class | Every route on the public server. **Any number** of them |
| **Controller** | `@InterceptWith(X.class)` on the controller class | Every route in that controller |
| **Method** | `@InterceptWith(X.class)` on a `@HttpRoute` method | That route only |

```java
// Server scope
@Tag(HttpServer.class)
@Component
public final class ServerInterceptor implements HttpServerInterceptor { /* ... */ }

// Controller and method scope
@Component
@HttpController
@InterceptWith(ControllerInterceptor.class)          // controller-level
public final class UserController {

    @InterceptWith(AuthInterceptor.class)            // method-level
    @HttpRoute(method = HttpMethod.POST, path = "/admin")
    public HttpServerResponse admin() { /* ... */ }
}
```

`@InterceptWith` (`io.koraframework.http.common.annotation`) is **repeatable** and carries an
optional `tag()` attribute for selecting a tagged implementation. The referenced type must be
resolvable from the graph, so annotate it `@Component` when it has dependencies to inject.

Kora 1.x allowed only one global interceptor. **2.0 accepts any number** — the router takes
`All<HttpServerInterceptor>`.

---

## Execution order

```
server interceptors → controller interceptors → method interceptors → handler
```

Server-scoped interceptors are sorted **by their class simple name**, not by declaration order:

```java
interceptorsList.sort(Comparator.comparing(i -> i.getClass().getSimpleName()));
```

So `AuthInterceptor` runs before `LoggingInterceptor` regardless of where each is declared. If
order matters, encode it in the class names (`A1Auth…`, `A2Logging…`) or collapse the logic into a
single interceptor — there is no `@Order` annotation. The chain is compiled once at construction
time and never changes.

Every interceptor must return the result of `chain.process(request)` (or a response of its own).
Dropping the result short-circuits the route.

---

## After and error logic

Everything is synchronous, so ordinary control flow works:

```java
@Override
public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
    // before
    try {
        HttpServerResponse response = chain.process(request);
        // after — response.code() is available
        return response;
    } catch (HttpServerResponseException e) {
        return e;                         // it IS an HttpServerResponse
    } catch (Exception e) {
        return HttpServerResponse.of(500, HttpBody.plaintext("failed"));
    } finally {
        // always
    }
}
```

`HttpServerResponseException extends RuntimeException implements HttpServerResponse`, so returning
the caught exception preserves the status and body the handler intended. There is no
`CompletionException` to unwrap any more. See [Error Handling](error-handling-reference.md).

---

## Enriching the request

`HttpServerRequest` has no attribute map, and `Context` no longer exists. To pass a value computed
in an interceptor down to the handler, derive a modified request and pass **that** to the chain:

```java
HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
    String userId = authenticate(request);
    return chain.process(request.toBuilder().header("x-user-id", userId).build());
}
```

The handler then reads it with `@Header`, or with an `HttpServerRequestMapper`. See
[Request Enrichment](context-propagation-reference.md) for the full pattern and its alternatives.

---

## Migration from 1.x

| 1.x | 2.0 |
|---|---|
| `ru.tinkoff.kora.http.server.common.HttpServerInterceptor` | `io.koraframework.http.server.common.interceptor.HttpServerInterceptor` |
| `@Tag(HttpServerModule.class)` | `@Tag(HttpServer.class)` — `io.koraframework.http.server.common.HttpServer` |
| `intercept(Context, HttpServerRequest, InterceptChain)` | `intercept(HttpServerRequest, InterceptChain)` |
| returns `CompletionStage<HttpServerResponse>` | returns `HttpServerResponse` |
| `chain.process(context, request)` | `chain.process(request)` |
| `.whenComplete(...)` / `.exceptionally(...)` | `try` / `catch` / `finally` |
| one global interceptor | any number |
| values passed via `Context.Key` | `request.toBuilder()` or a `ScopedValue` |

---

## Pitfalls

| Symptom | Cause and fix |
|---|---|
| Interceptor never runs, build is clean | Tagged `@Tag(HttpServerModule.class)` or untagged. Use `@Tag(HttpServer.class)` |
| Interceptor not applied to probes/metrics | By design — the system router collects `@Tag(SystemApi.class)`, not `@Tag(HttpServer.class)` |
| Interceptors run in an unexpected order | They are sorted by class simple name, not declaration order |
| Request hangs or the response is empty | The interceptor did not return `chain.process(request)` |
| `No component found for dependency: X` after `@InterceptWith(X.class)` | Add `@Component` to `X` |
| `error: exception IOException is never thrown…` | `JsonWriter.toByteArray` no longer declares a checked exception — remove the `catch` |
| Compile error on `Context` | `Context` was removed from the whole framework, not just the HTTP API |

**See also:** [Error Handling](error-handling-reference.md), [Request Enrichment](context-propagation-reference.md), [Authentication](authentication-reference.md).
