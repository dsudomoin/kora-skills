# Token Cache Reference

A single-flight, thread-safe token store for `HttpClientTokenProvider` implementations.

## Contents

- [What changed in 2.0](#what-changed-in-20)
- [The component (Java)](#the-component-java)
- [The component (Kotlin)](#the-component-kotlin)
- [Using it from a provider](#using-it-from-a-provider)
- [Why it is shaped this way](#why-it-is-shaped-this-way)
- [Invalidating on 401](#invalidating-on-401)
- [Two credentials, two caches](#two-credentials-two-caches)
- [Do not reach for the Kora cache module](#do-not-reach-for-the-kora-cache-module)
- [Testing](#testing)
- [See also](#see-also)

---

## What changed in 2.0

The 1.x cache returned `CompletionStage<String>` and composed refreshes with `thenApply`. In 2.0
`HttpClientTokenProvider.getToken` returns a plain `@Nullable String`, so the cache returns a
`String` and the refresh **blocks** the calling virtual thread. That is the intended model — the
one thing that still needs care is that a hundred concurrent callers must trigger **one** refresh,
not a hundred.

The pattern below is a plain fast-path read, then a `ReentrantLock` with a re-check inside it.
Nothing Kora-specific; it is here because getting it wrong is the most common way to turn a
working auth setup into a rate-limit incident.

---

## The component (Java)

```java
package com.example.client.auth;

import io.koraframework.common.annotation.Component;
import org.jspecify.annotations.Nullable;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Supplier;

@Component
public final class TokenCache {

    public record Token(String value, Instant expiresAt) {

        public static Token of(String value, Duration lifetime) {
            return new Token(value, Instant.now().plus(lifetime));
        }

        public boolean isFreshFor(Duration margin) {
            return Instant.now().plus(margin).isBefore(expiresAt);
        }
    }

    private static final Duration REFRESH_MARGIN = Duration.ofSeconds(60);

    private final ReentrantLock lock = new ReentrantLock();

    private volatile @Nullable Token token;

    public String getOrRefresh(Supplier<Token> refresh) {
        var cached = this.token;
        if (cached != null && cached.isFreshFor(REFRESH_MARGIN)) {
            return cached.value();
        }

        lock.lock();
        try {
            cached = this.token;
            if (cached != null && cached.isFreshFor(REFRESH_MARGIN)) {
                return cached.value();
            }
            var fresh = refresh.get();
            this.token = fresh;
            return fresh.value();
        } finally {
            lock.unlock();
        }
    }

    public void invalidate() {
        this.token = null;
    }
}
```

Notes on the Java shape:

- `@Nullable` is `org.jspecify.annotations.Nullable` and it is a **type-use** annotation. On a
  field it goes immediately before the type: `private volatile @Nullable Token token;`.
  `jakarta.annotation.Nullable` is not what Kora 2.0 uses.
- One `volatile` reference to an immutable record, not two mutable fields. Two `volatile` fields
  can be read in a torn combination (a new token with an old expiry); a single record cannot.
- The refresh runs **inside** the lock so exactly one caller performs it.

---

## The component (Kotlin)

```kotlin
package com.example.client.auth

import io.koraframework.common.annotation.Component
import java.time.Duration
import java.time.Instant
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

@Component
class TokenCache {

    data class Token(val value: String, val expiresAt: Instant) {
        fun isFreshFor(margin: Duration): Boolean = Instant.now().plus(margin).isBefore(expiresAt)

        companion object {
            fun of(value: String, lifetime: Duration) = Token(value, Instant.now().plus(lifetime))
        }
    }

    private val lock = ReentrantLock()

    @Volatile
    private var token: Token? = null

    fun getOrRefresh(refresh: () -> Token): String {
        token?.takeIf { it.isFreshFor(REFRESH_MARGIN) }?.let { return it.value }

        return lock.withLock {
            token?.takeIf { it.isFreshFor(REFRESH_MARGIN) }?.value
                ?: refresh().also { token = it }.value
        }
    }

    fun invalidate() {
        token = null
    }

    private companion object {
        val REFRESH_MARGIN: Duration = Duration.ofSeconds(60)
    }
}
```

Nullability is the type (`Token?`); do not carry JSpecify annotations into Kotlin.
`kotlin.concurrent.withLock` is in the standard library. Do **not** make `getOrRefresh` `suspend`
and do not wrap it in `runBlocking`/`Dispatchers.IO` — it is called from a synchronous Kora
contract already running on a virtual thread.

---

## Using it from a provider

```java
@Component
public final class CachingTokenProvider implements HttpClientTokenProvider {

    private final TokenCache cache;
    private final AuthClient authClient;

    public CachingTokenProvider(TokenCache cache, AuthClient authClient) {
        this.cache = cache;
        this.authClient = authClient;
    }

    @Override
    public String getToken(HttpClientRequest request) {
        return cache.getOrRefresh(() -> {
            var response = authClient.requestToken();
            return TokenCache.Token.of(response.accessToken(), Duration.ofSeconds(response.expiresIn()));
        });
    }
}
```

The supplier returns the token **and** its real lifetime together, so the cache never has to guess
an expiry. A cache that stores the value and separately assumes "probably an hour" is how a service
starts sending expired tokens the day the issuer shortens `expires_in`.

---

## Why it is shaped this way

| Decision | Reason |
|---|---|
| Fast path outside the lock | The common case is a valid cached token; taking a lock for every outbound request serialises the whole client |
| Re-check inside the lock | Without it, every thread that queued on the lock still performs its own refresh |
| Refresh margin (60 s) | A token that is valid *now* can expire while the request is in flight. Refresh before expiry, not at it |
| `Instant.now().plus(margin).isBefore(expiresAt)` | Positive test for freshness — easier to read than the negated "is expiring soon" and has no off-by-one at the boundary |
| Immutable `Token` record | One `volatile` write publishes value and expiry atomically |
| `ReentrantLock` over `synchronized` | Both are correct here; the lock is explicit about scope and adds `tryLock`/timeouts if you later need them |

The lock is held across a network call, so a refresh does make concurrent callers wait. That is the
point: they wait for one fetch instead of starting a hundred. If waiting is unacceptable, keep
serving the stale-but-not-expired token — that is exactly what the 60 s margin buys you.

---

## Invalidating on 401

`invalidate()` exists so a `401` can force the next caller to re-fetch instead of serving the same
rejected token until the margin elapses:

```java
var response = chain.process(authorized);
if (response.code() == 401) {
    response.close();
    cache.invalidate();
    return chain.process(reauthorize(request));
}
return response;
```

Retry **once**. A loop that retries until success turns a revoked credential into an infinite
request storm against the auth server. See
[jwt-token-provider-reference.md](jwt-token-provider-reference.md).

---

## Two credentials, two caches

`TokenCache` holds one token. Two clients with different credentials need two instances,
disambiguated by `@Tag` rather than by copying the class:

```java
public final class OrdersApi {}
public final class BillingApi {}
```

```java
@Module
public interface TokenCachesModule {

    @Tag(OrdersApi.class)
    default TokenCache ordersTokenCache() {
        return new TokenCache();
    }

    @Tag(BillingApi.class)
    default TokenCache billingTokenCache() {
        return new TokenCache();
    }
}
```

```java
@Component
public final class OrdersTokenProvider implements HttpClientTokenProvider {

    public OrdersTokenProvider(@Tag(OrdersApi.class) TokenCache cache, OrdersAuthClient authClient) { … }
}
```

Remove `@Component` from `TokenCache` when you provide it from a `@Module` this way — keeping both
gives `Multiple components match dependency: TokenCache (no tags)`.

---

## Do not reach for the Kora cache module

`@Cacheable` and the `cache-caffeine` / `cache-redis-lettuce` modules are for caching **method
results keyed by arguments**. A single access token is not that shape:

- there is no key — `getToken(request)` returns the same value for every request;
- the entry's lifetime comes from the response (`expires_in`), not from a fixed
  `expireAfterWrite` in config;
- a shared Redis entry would put a bearer token in a store other services can read.

Keep the token in memory, per process. See
[kora-aop-caching](../../kora-aop-caching/SKILL.md) for the cases that module *is* for.

---

## Testing

`TokenCache` has no Kora dependencies, so test it directly:

```java
@Test
void servesTheCachedTokenUntilTheMargin() {
    var cache = new TokenCache();
    var fetches = new AtomicInteger();

    Supplier<TokenCache.Token> refresh = () ->
            TokenCache.Token.of("token-" + fetches.incrementAndGet(), Duration.ofHours(1));

    assertThat(cache.getOrRefresh(refresh)).isEqualTo("token-1");
    assertThat(cache.getOrRefresh(refresh)).isEqualTo("token-1");
    assertThat(fetches).hasValue(1);

    cache.invalidate();
    assertThat(cache.getOrRefresh(refresh)).isEqualTo("token-2");
}

@Test
void refreshesAheadOfExpiry() {
    var cache = new TokenCache();
    var fetches = new AtomicInteger();

    // 30s lifetime is inside the 60s refresh margin, so it is never considered fresh
    Supplier<TokenCache.Token> refresh = () ->
            TokenCache.Token.of("token-" + fetches.incrementAndGet(), Duration.ofSeconds(30));

    cache.getOrRefresh(refresh);
    cache.getOrRefresh(refresh);
    assertThat(fetches).hasValue(2);
}

@Test
void concurrentCallersTriggerOneRefresh() throws Exception {
    var cache = new TokenCache();
    var fetches = new AtomicInteger();
    var start = new CountDownLatch(1);

    try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
        var futures = IntStream.range(0, 64)
                .mapToObj(i -> executor.submit(() -> {
                    start.await();
                    return cache.getOrRefresh(() -> {
                        fetches.incrementAndGet();
                        return TokenCache.Token.of("token", Duration.ofHours(1));
                    });
                }))
                .toList();
        start.countDown();
        for (var f : futures) {
            assertThat(f.get()).isEqualTo("token");
        }
    }

    assertThat(fetches).hasValue(1);
}
```

The third test is the one worth keeping: it fails on every cache that drops the re-check inside the
lock, and passes on every cache that has it.

---

## See also

- [jwt-token-provider-reference.md](jwt-token-provider-reference.md) — the provider around this cache
- [oauth2-client-credentials-reference.md](oauth2-client-credentials-reference.md) — full client-credentials wiring
- [http-client-auth-reference.md](http-client-auth-reference.md) — attaching the Bearer interceptor
