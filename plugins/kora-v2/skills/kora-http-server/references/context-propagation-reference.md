# Request Enrichment Reference

Passing a value computed in an interceptor down to the handler, now that `Context` is gone.

> **`ru.tinkoff.kora.common.Context` does not exist in Kora 2.0.** It was not moved or renamed — it
> was removed from the entire framework. `Context.Key`, `Context.current()`, `context.get(key)` and
> the `Context` parameter on interceptors, request mappers and response mappers are all gone. Every
> 1.x pattern built on it needs one of the three replacements below.

## Contents

- [Choosing a mechanism](#choosing-a-mechanism)
- [1. Enrich the request](#1-enrich-the-request)
- [2. ScopedValue](#2-scopedvalue)
- [3. Principal, for authorization](#3-principal-for-authorization)
- [Migration from Context](#migration-from-context)
- [Pitfalls](#pitfalls)

---

## Choosing a mechanism

| Need | Use |
|---|---|
| Authenticated caller identity | `Principal` — see [Authentication](authentication-reference.md) |
| A value the handler can declare as a parameter | [Enrich the request](#1-enrich-the-request) with `toBuilder()` |
| A value read deep in the call stack, not at the handler boundary | [`ScopedValue`](#2-scopedvalue) |

Prefer the first two. A value that a handler declares as a parameter is visible in the signature
and testable without any ambient state; a `ScopedValue` is invisible ambient state and should be
reserved for genuinely cross-cutting values (trace ids, tenant, principal).

---

## 1. Enrich the request

`chain.process(...)` takes the request to pass downstream, and `HttpServerRequest.toBuilder()`
derives a modified copy. So an interceptor can attach a computed value as a header and the handler
binds it like any other parameter.

```java
@Tag(HttpServer.class)
@Component
public final class TenantInterceptor implements HttpServerInterceptor {

    private final TenantResolver resolver;

    public TenantInterceptor(TenantResolver resolver) {
        this.resolver = resolver;
    }

    @Override
    public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
        String tenant = resolver.resolve(request.headers().getFirst("host"));
        if (tenant == null) {
            return HttpServerResponseException.of(400, "Unknown tenant");
        }
        return chain.process(request.toBuilder()
                .headerRemove("x-tenant-id")      // never trust an inbound value
                .header("x-tenant-id", tenant)
                .build());
    }
}
```

```java
@HttpRoute(method = HttpMethod.GET, path = "/reports")
@Json
public List<Report> reports(@Header("x-tenant-id") String tenantId) {
    return reportService.forTenant(tenantId);
}
```

`HttpServerRequestBuilder` offers `header(name, value)`, `header(name, List<String>)`,
`headerRemove(name)`, `queryParam(name[, value])` (with `int`/`long`/`boolean`/`UUID`/`Collection`
overloads), `queryParamRemove(name)` and `body(HttpBodyInput)`.

**Always `headerRemove` before `header`** for a value the handler will trust. Otherwise a client
can send `x-tenant-id` itself, and depending on how the header list is assembled the handler may
read the attacker's value.

For a richer value, pair the enrichment with an `HttpServerRequestMapper` so the handler receives a
typed object rather than loose strings:

```java
@Component
public static final class TenantContextMapper implements HttpServerRequestMapper<TenantContext> {
    @Override
    public TenantContext apply(HttpServerRequest request) {
        return new TenantContext(request.headers().getFirst("x-tenant-id"),
                                 request.headers().getFirst("x-trace-id"));
    }
}

@HttpRoute(method = HttpMethod.GET, path = "/reports")
@Json
public List<Report> reports(@Mapping(TenantContextMapper.class) TenantContext ctx) { /* ... */ }
```

Remember the `@Component` rule: `@Mapping(X.class)` injects the concrete class, so `X` must be a
component even with no constructor arguments. See
[Response Types](response-types-reference.md#the-component-rule-for-mappers).

---

## 2. ScopedValue

Kora 2.0 handlers run on virtual threads, and `ScopedValue` (final API in JDK 25, no preview flag
needed) is the JDK's replacement for a `ThreadLocal` in that model. This is the mechanism Kora
itself uses for `Principal`.

```java
public final class RequestScope {
    public static final ScopedValue<String> TRACE_ID = ScopedValue.newInstance();

    public static String traceId() {
        return TRACE_ID.isBound() ? TRACE_ID.get() : null;
    }
}
```

```java
@Tag(HttpServer.class)
@Component
public final class TraceInterceptor implements HttpServerInterceptor {

    @Override
    public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
        String traceId = Objects.requireNonNullElseGet(
                request.headers().getFirst("x-trace-id"),
                () -> UUID.randomUUID().toString());

        return ScopedValue.where(RequestScope.TRACE_ID, traceId)
                .call(() -> chain.process(request));
    }
}
```

Anything called during that request — the handler, services below it, another interceptor further
down the chain — reads it with `RequestScope.traceId()`.

Rules that come with `ScopedValue`:

- **Always check `isBound()`** before `get()`; an unbound read throws `NoSuchElementException`.
  Code reachable outside a request (a scheduled job, a Kafka consumer, a test) will hit that.
- The binding is immutable for the duration of the scope. You cannot set a value from inside the
  handler and read it in an outer interceptor — bind it where the scope starts.
- The value does **not** propagate to threads you start yourself, only to `StructuredTaskScope`
  forks within the scope.

Do not reach for `ThreadLocal` instead: virtual threads make per-request `ThreadLocal`s expensive
and easy to leak.

---

## 3. Principal, for authorization

For the authenticated caller specifically, use the framework's own contract rather than rolling
your own scoped value:

```java
public interface Principal {
    ScopedValue<Principal> VALUE = ScopedValue.newInstance();

    @Nullable static Principal current();
    static <T, X extends Throwable> T with(Principal principal, ScopedValue.CallableOp<T, X> op) throws X;
}
```

An interceptor binds it, and anything below reads `Principal.current()`:

```java
return Principal.with(principal, () -> chain.process(request));
```

That is exactly what the OpenAPI-generated `ApiSecurity` interceptors do. See
[Authentication](authentication-reference.md) for how the pieces fit together and which skill owns
the detail.

---

## Migration from Context

| 1.x | 2.0 |
|---|---|
| `Context.Key<T> KEY = Context.key()` | `ScopedValue<T> KEY = ScopedValue.newInstance()` |
| `ctx.set(KEY, value)` before `chain.process(ctx, req)` | `ScopedValue.where(KEY, value).call(() -> chain.process(req))` |
| `Context.current().get(KEY)` | `KEY.isBound() ? KEY.get() : null` |
| `Context` parameter on `intercept` / mappers | removed from every signature |
| auth principal stashed in `Context` | `Principal.with(...)` / `Principal.current()` |
| value only needed by the handler | a header via `request.toBuilder()` + `@Header` |

A `Context` import that survives a package rename is a compile error, not a silent failure — but
the *design* it implies still has to change, because setting a value after the chain has started is
no longer possible.

---

## Pitfalls

| Symptom | Cause and fix |
|---|---|
| `cannot find symbol: class Context` | `Context` was removed framework-wide; pick a mechanism above |
| `NoSuchElementException` from `get()` | The `ScopedValue` is unbound — guard with `isBound()` |
| Handler reads a header the client controls | Call `headerRemove(name)` before `header(name, value)` |
| Value set in the handler is invisible to the interceptor | Bindings are immutable; bind at the start of the scope |
| Value missing in a forked task | Only `StructuredTaskScope` forks inherit the binding |
| Value missing in a test | The test calls the handler directly, outside any interceptor — bind it explicitly or pass it as a parameter |

**See also:** [Interceptors](interceptors-reference.md), [Request Mapping](request-mapping-reference.md), [Authentication](authentication-reference.md).
