# Imperative cache use

**Contracts:** `io.koraframework.cache.{Cache, LoadableCache}`
**Backends:** `CaffeineCache<K, V>` and `RedisCache<K, V>` both extend `Cache<K, V>`

Injecting a `@Cache` interface and calling it directly is a first-class alternative to the
annotations. Use it when the key is produced inside the method body, when the cache holds state
rather than a memoised result (one-time tokens, counters, in-flight OAuth state), or when caching
must be conditional.

---

## Contents

- [The Cache contract](#the-cache-contract)
- [Injecting a cache](#injecting-a-cache)
- [Null handling](#null-handling)
- [computeIfAbsent](#computeifabsent)
- [Bulk operations](#bulk-operations)
- [LoadableCache](#loadablecache)
- [Facade cache](#facade-cache)
- [Backend-specific methods](#backend-specific-methods)
- [Patterns](#patterns)
- [Imperative vs declarative](#imperative-vs-declarative)

---

## The `Cache` contract

```java
public interface Cache<K, V> {

    @Nullable V get(K key);
    Map<K, V> get(Collection<K> keys);

    V put(K key, V value);
    Map<K, V> put(Map<K, V> keyAndValues);

    @Nullable V computeIfAbsent(K key, Function<K, @Nullable V> mappingFunction);
    Map<K, V> computeIfAbsent(Collection<K> keys, Function<Set<K>, Map<K, V>> mappingFunction);

    void invalidate(K key);
    void invalidate(Collection<K> keys);
    void invalidateAll();

    default LoadableCache<K, V> asLoadableSimple(Function<K, V> cacheLoader);
    default LoadableCache<K, V> asLoadable(Function<Collection<K>, Map<K, V>> cacheLoader);

    static <K, V> Builder<K, V> builder(Cache<K, V> cache);
}
```

That is the whole surface. There is no `contains`, no `containsValue`, no `asMap`, no `size` and no
`CacheManager`. `CaffeineCache` adds `getAll()`; `RedisCache` adds `putExpireAfterWrite(...)`.

Note that `put` **returns** the value it stored (and `put(Map)` returns the map), which makes
`return cache.put(id, loaded);` a legitimate one-liner.

---

## Injecting a cache

The `@Cache` interface is a normal graph component — constructor-inject it.

```java
@Component
public class OrderService {

    private final OrderCache cache;
    private final OrderRepository repository;

    public OrderService(OrderCache cache, OrderRepository repository) {
        this.cache = cache;
        this.repository = repository;
    }

    public OrderDto getOrLoad(UUID id) {
        return cache.computeIfAbsent(id, repository::find);
    }
}
```

```kotlin
@Component
class OrderService(
    private val cache: OrderCache,
    private val repository: OrderRepository
) {
    fun getOrLoad(id: UUID): OrderDto? = cache.computeIfAbsent(id) { repository.find(it) }
}
```

A class that only uses the cache imperatively needs no AOP, so it does not have to be `open` in
Kotlin or non-final in Java.

---

## Null handling

Both backends treat null defensively and never throw for it:

| Call | Behaviour with null |
|---|---|
| `get(null)` | returns `null` |
| `put(null, v)` / `put(k, null)` | **no-op**; Caffeine returns the value, Redis returns `null` |
| `computeIfAbsent(null, fn)` | calls `fn` and returns its result without caching |
| loader returns `null` | nothing is stored; the next call loads again |
| `invalidate(null)` | no-op |

So "the value was not cached" is never signalled by an exception. If a null value must be
distinguishable from a miss, cache `Optional<V>`:

```java
@Cache("orders.cache")
public interface OrderCache extends CaffeineCache<UUID, Optional<OrderDto>> {}
```

The `@Cacheable` aspect understands that shape too, and it is the only way to memoise a negative
lookup.

---

## `computeIfAbsent`

`computeIfAbsent(key, loader)` is the imperative equivalent of `@Cacheable` on a single cache — the
aspect generates exactly this call. On Caffeine it runs under the cache's own per-key lock, so the
loader executes once for concurrent callers. On Redis it is `get` → loader → `put`, with no
distributed lock.

```java
public OrderDto get(UUID id) {
    return cache.computeIfAbsent(id, key -> repository.find(key));
}
```

---

## Bulk operations

```java
Map<UUID, OrderDto> found = cache.get(List.of(id1, id2, id3));      // only the entries present

Map<UUID, OrderDto> all = cache.computeIfAbsent(
        List.of(id1, id2, id3),
        missing -> repository.findAll(missing));                     // loader gets only the misses

cache.put(Map.of(id1, dto1, id2, dto2));
cache.invalidate(List.of(id1, id2));
```

The bulk loader receives a `Set<K>` of the keys that were **not** found and must return a map for
them; keys it omits simply stay uncached. Redis uses `MGET`/`MSET` (or `GETEX`/`PSETEX` when a TTL
is configured) for these.

---

## `LoadableCache`

A read-only get-or-load view:

```java
public interface LoadableCache<K, V> {
    @Nullable V get(K key);
    Map<K, V> get(Collection<K> keys);
}
```

Pick the factory that matches your loader shape:

```java
cache.asLoadableSimple(repository::find);                    // Function<K, V>
cache.asLoadable(keys -> repository.findAll(keys));          // Function<Collection<K>, Map<K, V>>
```

Passing a single-key method reference to `asLoadable` does not compile — that overload is the bulk
one.

Publish it from a module so it can be injected:

```java
@KoraApp
public interface Application extends HoconConfigModule, CaffeineCacheModule {

    default LoadableCache<UUID, OrderDto> orderLoadableCache(OrderCache cache, OrderRepository repository) {
        return cache.asLoadableSimple(repository::find);
    }
}
```

`LoadableCache` exposes no writes — keep the underlying `Cache` injected as well if you also need
`put` or `invalidate`.

---

## Facade cache

`Cache.builder` composes several caches into one `Cache<K, V>` with L1/L2 semantics:

```java
Cache<String, UserResponse> layered = Cache.builder(userCaffeineCache)
        .addCache(userRedisCache)
        .build();
```

- `get` walks the levels in order and returns the first hit — without back-filling.
- `computeIfAbsent` walks the levels, back-fills every shallower level on a hit, and on a full miss
  writes the computed value into all of them.
- `put`, `invalidate`, `invalidateAll` fan out to every level.
- `get(Collection<K>)` throws `UnsupportedOperationException`; use
  `computeIfAbsent(Collection, Function)` for bulk reads.
- A builder with one cache returns that cache unchanged.

For a declarative L1/L2 prefer stacked annotations —
[multi-level-cache-reference.md](multi-level-cache-reference.md).

---

## Backend-specific methods

```java
Map<K, V> CaffeineCache.getAll();                                          // every live entry

V RedisCache.putExpireAfterWrite(K key, V value, Duration ttl);            // per-call TTL
Map<K, V> RedisCache.putExpireAfterWrite(Map<K, V> values, Duration ttl);
```

`putExpireAfterWrite` rejects a null `Duration` with
`RedisCache#putExpireAfterWrite received nullable expireAfterWrite argument`.

Redis swallows client errors: a failed `get` reads as a miss, a failed `put` returns normally. Do
not build correctness on a Redis write having happened.

---

## Patterns

### One-time token

```java
@Cache("oauth.state")
public interface OAuthStateCache extends CaffeineCache<String, OAuthState> {}
```

```java
public String issue() {
    var state = UUID.randomUUID().toString();
    stateCache.put(state, new OAuthState(state, Instant.now()));
    return state;
}

public OAuthState consume(String state) {
    var found = stateCache.get(state);
    if (found == null) {
        throw new IllegalStateException("Unknown or expired OAuth state");
    }
    stateCache.invalidate(state);           // one-time use
    return found;
}
```

with `oauth.state { maximumSize = 10000, expireAfterWrite = "5m" }` — the TTL, not application code,
enforces expiry.

### Cache warm-up on create

The key does not exist until the method has run, so `@CachePut` cannot express this:

```java
public OrderDto create(OrderRequest request) {
    var created = repository.insert(request);
    cache.put(created.id(), created);
    return created;
}
```

### Conditional caching

Kora's annotations have no `condition` / `unless`, so gate it in code:

```java
public OrderDto get(UUID id, boolean fresh) {
    if (fresh) {
        return repository.find(id);
    }
    return cache.computeIfAbsent(id, repository::find);
}
```

---

## Imperative vs declarative

| | Declarative (`@Cacheable` …) | Imperative (`Cache<K, V>`) |
|---|---|---|
| Key | derived from method parameters / `@Mapping` | anything you can compute |
| Conditional caching | not supported | trivial |
| Concurrency | `computeIfAbsent` for a single sync cache | whatever you call |
| Multi-level | stacked annotations | `Cache.builder` or by hand |
| AOP requirements | non-final (Java) / `open` (Kotlin), no self-invocation | none |
| Async writes | `mode = CacheMode.ASYNC` | your own executor |

The two mix freely on the same cache: annotate the read path and write to the same injected cache
from a `create` method.

---

## See also

- [cacheable-reference.md](cacheable-reference.md) — the annotations these calls replace
- [cache-caffeine-reference.md](cache-caffeine-reference.md) — Caffeine configuration
- [cache-redis-reference.md](cache-redis-reference.md) — Redis configuration
- [multi-level-cache-reference.md](multi-level-cache-reference.md) — L1/L2 patterns
