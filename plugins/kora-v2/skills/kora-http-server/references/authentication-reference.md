# Authentication Reference

Where authentication lives in Kora 2.0 and how it attaches to this HTTP server.

> **The detail lives in the `kora-http-server-auth` sub-skill.** This page covers only the
> server-side seams — the contracts, where they plug in, and what changed from 1.x — so that a
> controller task does not have to load the whole auth skill. For scheme-specific work (Bearer/JWT,
> Basic, API key, OpenAPI `securitySchemes`, scope checks, 401 vs 403) read that skill.

## Contents

- [What changed from 1.x](#what-changed-from-1x)
- [The contracts](#the-contracts)
- [How it attaches to the server](#how-it-attaches-to-the-server)
- [Reading the principal in a handler](#reading-the-principal-in-a-handler)
- [Hand-rolled auth without OpenAPI](#hand-rolled-auth-without-openapi)
- [Pitfalls](#pitfalls)

---

## What changed from 1.x

The 1.x guidance in this skill said: do **not** use `Principal` as a controller parameter, because
`HttpServerPrincipalExtractor` was not bridged and the request degraded from 401 to 400; stash the
principal in a `Context` key from a global interceptor instead.

**That advice is obsolete on both halves.**

- `Context` no longer exists, so the workaround is unimplementable.
- `Principal` is now backed by a JDK `ScopedValue` and is read with `Principal.current()` from
  anywhere inside the request — no controller parameter and no `Context` key needed.

| 1.x | 2.0 |
|---|---|
| `ru.tinkoff.kora.common.Principal` | `io.koraframework.common.Principal` |
| `…http.common.auth.PrincipalWithScopes` | `io.koraframework.http.common.auth.PrincipalWithScopes` |
| `…http.server.common.auth.HttpServerPrincipalExtractor` | `io.koraframework.http.server.common.auth.HttpServerPrincipalExtractor` |
| principal stashed in a `Context` key | `Principal.with(...)` binds a `ScopedValue`; `Principal.current()` reads it |
| `@Tag(HttpServerModule.class)` on the auth interceptor | `@Tag(HttpServer.class)` |
| ordinal `SecurityRequirementTagN` markers (OpenAPI) | tags named after the security scheme, e.g. `@Tag(ApiSecurity.BearerAuth.class)` |

---

## The contracts

```java
package io.koraframework.common;

public interface Principal {
    ScopedValue<Principal> VALUE = ScopedValue.newInstance();

    @Nullable static Principal current();
    static <T, X extends Throwable> T with(Principal principal, ScopedValue.CallableOp<T, X> op) throws X;
}
```

```java
package io.koraframework.http.common.auth;

public interface PrincipalWithScopes extends Principal {
    Collection<String> scopes();
}
```

```java
package io.koraframework.http.server.common.auth;

public interface HttpServerPrincipalExtractor<T, P extends Principal> {
    @Nullable P extract(HttpServerRequest request, @Nullable T token);
}
```

`T` is the raw credential the transport produced (a `String` for Bearer, API key, Basic and
Cookie schemes); `P` is your principal type.

```java
public record UserContext(String userId, String traceId) implements Principal {}

@Component
@Tag(ApiSecurity.BearerAuth.class)
public final class UserContextExtractor implements HttpServerPrincipalExtractor<String, UserContext> {

    private final TokenService tokens;

    public UserContextExtractor(TokenService tokens) {
        this.tokens = tokens;
    }

    @Override
    public UserContext extract(HttpServerRequest request, @Nullable String token) {
        if (token == null) {
            return null;                       // no principal -> 401 from the generated interceptor
        }
        return new UserContext(tokens.userId(token), request.headers().getFirst("x-trace-id"));
    }
}
```

---

## How it attaches to the server

Authentication is an **interceptor**, not a separate subsystem. With an OpenAPI contract the
generator emits one `ApiSecurity` interceptor per security scheme, each of which resolves the
credential, calls your extractor, and binds the result for the rest of the request:

```java
return Principal.with(bearerPrincipal, () -> chain.process(request));
```

Those interceptors are attached to the generated controllers with `@InterceptWith`, and the
extractor is selected by `@Tag(ApiSecurity.<SchemeName>.class)` — the tag is named after the
scheme in the contract, not by ordinal position.

Because it is an ordinary interceptor, everything in
[Interceptors](interceptors-reference.md) applies: it never runs on the system server, and a
hand-written server-scoped one needs `@Tag(HttpServer.class)`.

---

## Reading the principal in a handler

```java
@HttpRoute(method = HttpMethod.GET, path = "/me")
@Json
public UserResponse me() {
    Principal principal = Principal.current();
    if (!(principal instanceof UserContext user)) {
        throw HttpServerResponseException.of(401, "Unauthenticated");
    }
    return userService.get(user.userId());
}
```

`Principal.current()` returns `null` when the `ScopedValue` is unbound — which is the case on any
route the auth interceptor does not cover, and in a unit test that calls the handler directly.
Always null-check; never assume a binding exists.

To assert scopes, extract `PrincipalWithScopes` and check `scopes()`.

---

## Hand-rolled auth without OpenAPI

Without a generated `ApiSecurity`, write the interceptor yourself and bind the principal the same
way:

```java
@Tag(HttpServer.class)
@Component
public final class AuthInterceptor implements HttpServerInterceptor {

    private final HttpServerPrincipalExtractor<String, UserContext> extractor;

    public AuthInterceptor(HttpServerPrincipalExtractor<String, UserContext> extractor) {
        this.extractor = extractor;
    }

    @Override
    public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
        String header = request.headers().getFirst("authorization");
        String token = (header != null && header.regionMatches(true, 0, "Bearer ", 0, 7))
                ? header.substring(7)
                : null;

        UserContext principal = extractor.extract(request, token);
        if (principal == null) {
            return HttpServerResponseException.of(401, "Unauthenticated");
        }
        return Principal.with(principal, () -> chain.process(request));
    }
}
```

Scope a narrower policy with `@InterceptWith(AuthInterceptor.class)` on a controller or a single
route instead of the server-wide tag.

---

## Pitfalls

| Symptom | Cause and fix |
|---|---|
| Auth interceptor never runs | Tagged `@Tag(HttpServerModule.class)` or untagged — use `@Tag(HttpServer.class)`, or apply it with `@InterceptWith` |
| `Principal.current()` is null in a handler | No interceptor bound it on this route, or the test calls the handler directly |
| `NoSuchElementException` reading the principal | Reading `Principal.VALUE.get()` directly instead of `Principal.current()` |
| `No component found: HttpServerPrincipalExtractor<...>` | The extractor is missing `@Component`, or its `@Tag` does not match the scheme the generated code requests |
| Probes are unauthenticated | Correct: the system router collects `@Tag(SystemApi.class)`, not `@Tag(HttpServer.class)`, so your interceptor never sees a probe. Restrict that port at the network level |
| Auth applied but 400 instead of 401 | An extractor that throws leaks through the generated 400 wrapping — return `null` (or throw an `HttpServerResponseException` with your own code) |

**See also:** [Interceptors](interceptors-reference.md), [Request Enrichment](context-propagation-reference.md), and the `kora-http-server-auth` sub-skill.
