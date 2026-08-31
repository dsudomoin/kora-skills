# Controller & Routing Reference

`@HttpController`, `@HttpRoute`, the 2.0 package layout, and how the router resolves a request.

## Contents

- [Package map](#package-map)
- [@HttpController](#httpcontroller)
- [@HttpRoute](#httproute)
- [Path composition](#path-composition)
- [Path variables](#path-variables)
- [Route resolution: 404 vs 405](#route-resolution-404-vs-405)
- [Generated code](#generated-code)
- [Common pitfalls](#common-pitfalls)

---

## Package map

Kora 2.0 renamed the group **and** re-split the HTTP packages. Renaming `ru.tinkoff.kora` to
`io.koraframework` alone leaves imports that do not resolve, because the type also moved.

| Type | Kora 1.x | Kora 2.0 |
|---|---|---|
| `HttpController` | `…http.server.common.annotation` | `io.koraframework.http.server.common.annotation` |
| `HttpRoute`, `Path`, `Query`, `Header`, `Cookie`, `InterceptWith` | `…http.common.annotation` | `io.koraframework.http.common.annotation` |
| `HttpMethod`, `HttpResponseEntity`, `HttpResultCode` | `…http.common` | `io.koraframework.http.common` |
| `HttpBody` | `…http.common.body` | `io.koraframework.http.common.body` |
| `HttpHeaders` | `…http.common.header` | `io.koraframework.http.common.header` |
| `HttpServerRequest`, `HttpServerRequestMapper` | `…http.server.common` | `io.koraframework.http.server.common.**request**` |
| `HttpServerResponse`, `HttpServerResponseException`, `HttpServerResponseMapper` | `…http.server.common` | `io.koraframework.http.server.common.**response**` |
| `HttpServerInterceptor` | `…http.server.common` | `io.koraframework.http.server.common.**interceptor**` |
| `HttpServerPrincipalExtractor` | `…http.server.common.auth` | `io.koraframework.http.server.common.auth` |
| `HttpServer` (interceptor tag) | — | `io.koraframework.http.server.common` |
| `Component`, `Tag`, `Mapping` | `ru.tinkoff.kora.common` | `io.koraframework.common.annotation` |

The request/response/interceptor split is the one that bites: those three types used to sit
directly in `http.server.common` and now each has its own subpackage.

---

## @HttpController

Marks a class as an HTTP controller. The class must also be `@Component` so it is registered in
the application graph.

```java
import io.koraframework.common.annotation.Component;
import io.koraframework.http.server.common.annotation.HttpController;

@Component
@HttpController
public final class UserController { /* ... */ }
```

`@HttpController` also accepts an optional **path prefix**:

```java
public @interface HttpController {
    String value() default "";   // "Describes the path prefix of HTTP handlers"
}
```

`@HttpController("/api")` + `@HttpRoute(path = "/files/*")` registers `/api/files/*`. Leave the
value off (the default `""`) and each route carries its own full path. See
[Path composition](#path-composition) for the Java/Kotlin difference in how the two halves are joined.

Routes are also collected from superclasses and implemented interfaces, so a controller may
inherit `@HttpRoute` methods from a shared interface.

---

## @HttpRoute

Binds an HTTP method and a path to a handler method.

```java
@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
public UserResponse getUser(@Path String id) { /* ... */ }
```

| Attribute | Type | Required | Description |
|---|---|---|---|
| `method` | `String` | yes | HTTP method |
| `path` | `String` | yes | Full request path, may contain `{var}` segments |

`HttpMethod` (`io.koraframework.http.common.HttpMethod`) is a **final class of `String`
constants**, not an enum:

```java
HttpMethod.GET  HttpMethod.HEAD    HttpMethod.POST    HttpMethod.PUT     HttpMethod.DELETE
HttpMethod.CONNECT  HttpMethod.OPTIONS  HttpMethod.TRACE  HttpMethod.PATCH  HttpMethod.QUERY
```

Because `method()` is declared as `String`, `@HttpRoute(method = "GET", path = "/x")` is equally
valid — the constants exist for readability. Do not write `HttpMethod.valueOf(...)` or treat it as
an enum type in a signature.

---

## Path composition

The registered route is the controller prefix followed by the route path. Either write the full
path on every route and leave `@HttpController` bare, or factor the shared prefix out:

```java
@Component
@HttpController("/users")
public final class UserController {

    @HttpRoute(method = HttpMethod.GET, path = "/{id}")          // GET /users/{id}
    public UserResponse getUser(@Path String id) { /* ... */ }

    @HttpRoute(method = HttpMethod.GET, path = "/{id}/orders")   // GET /users/{id}/orders
    public List<OrderResponse> getOrders(@Path String id) { /* ... */ }
}
```

**Java and Kotlin join the two halves differently — this is a real portability trap.**

| | Java (`HttpServerUtils.extract`) | Kotlin (`RouteProcessor.extractRoute`) |
|---|---|---|
| Prefix | trimmed; a leading `/` is added if missing; a trailing `/` is stripped | trimmed only |
| Route path | a leading `/` is added if missing | used as-is |
| Result | normalised concatenation | plain `"$rootPath$path"` |

So `@HttpController("api")` + `path = "users"` gives `/api/users` in Java and `apiusers` in Kotlin.
**In Kotlin always write the prefix with a leading slash and no trailing slash** (`"/api"`), and
start each route path with `/`. Written that way both processors produce the same route.

`httpServer.ignoreTrailingSlash` (default `false`) controls whether `/users` and `/users/` match
the same route. When enabled the router registers both variants, except for terminal-wildcard
templates.

---

## Path variables

```java
@HttpRoute(method = HttpMethod.GET, path = "/users/{userId}/orders/{orderId}")
public OrderResponse getOrder(@Path String userId, @Path String orderId) { /* ... */ }
```

The `@Path` name defaults to the argument name; use `@Path("orderId") String oid` only when they
differ. The name must match a `{...}` segment exactly.

A path may also end in a terminal wildcard `*`, which matches the remainder of the path.

---

## Route resolution: 404 vs 405

The router builds one matcher per HTTP method plus a method-independent matcher, all at
construction time. For a request it:

1. selects the matcher for the request method and matches the path (exact, parameter, then
   terminal-wildcard semantics);
2. if no method-specific route matches, consults the method-independent matcher — a path that
   exists under a *different* method yields **405 Method Not Allowed**, an unknown path yields
   **404 Not Found**;
3. wraps the request in a `RoutedHttpServerRequest` carrying the matched template and captured
   path parameters;
4. runs the interceptor chain, then the handler.

The router is immutable after construction and performs no locking on the request path. Routes
cannot be added at runtime.

Two routes on the same HTTP method whose templates are equivalent (for example `/a/{x}` and
`/a/{y}` — parameter names do not distinguish them) fail at startup with:

```
Cannot add path template /a/{y}, matcher already contains an equivalent pattern /a/{x}
```

---

## Generated code

For `UserController` the processor emits `UserControllerModule`, a `@Module` interface with one
factory method per route:

```java
@Generated("io.koraframework.http.server.annotation.processor.ControllerModuleGenerator")
@Module
public interface UserControllerModule {
  default HttpServerRequestHandler get_users_userId(UserController _controller,
                                                    HttpServerResponseMapper<UserResponse> _responseMapper) {
    return HttpServerRequestHandlerImpl.of("GET", "/users/{userId}", (_request) -> {
      final String userId;
      try {
        userId = HttpRequestHandlerUtils.parsePathString(_request, "userId");
      } catch (Exception _e) {
        if (_e instanceof HttpServerResponse) throw _e;
        else throw HttpServerResponseException.of(400, _e);
      }
      var _result = _controller.getUser(userId);
      return _responseMapper.apply(_request, _result);
    });
  }
}
```

Two things are worth reading off the generated source when debugging:

- **the parameter list** tells you exactly which components the route demands from the graph
  (the controller, and a mapper only when one is actually needed);
- **parameter parsing failures become `400`**, automatically, unless the thrown value is itself an
  `HttpServerResponse`.

Generated sources land in `build/generated/sources/annotationProcessor/java/main/` (Java) or
`build/generated/ksp/main/kotlin/` (Kotlin). Read them; never edit them. After a package rename,
stale generated files cause phantom errors — run `clean` rather than patching them.

---

## Common pitfalls

### 404 on a valid path

The `{var}` in the route has no matching `@Path` argument, or the `@HttpController` prefix did not
compose into the path you expected (see [Path composition](#path-composition) — in Kotlin it is
concatenated raw). The registered template is printed by the generated `<Controller>Module`; read
it there rather than guessing.

```java
// Wrong: route declares {id} but there is no @Path argument
@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
public UserResponse getUser() { /* ... */ }

// Correct
@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
public UserResponse getUser(@Path String id) { /* ... */ }
```

### Name mismatch between `{segment}` and `@Path`

```java
// Wrong: {id} in the path, but the binding names "userId"
@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
public UserResponse getUser(@Path("userId") String userId) { /* ... */ }

// Correct
@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
public UserResponse getUser(@Path String id) { /* ... */ }
```

### Controller not reachable at all

The class is `@HttpController` but not `@Component`, so neither it nor its generated module joins
the graph and every route 404s.

**See also:** [Request Mapping](request-mapping-reference.md), [Response Types](response-types-reference.md), [Interceptors](interceptors-reference.md).
