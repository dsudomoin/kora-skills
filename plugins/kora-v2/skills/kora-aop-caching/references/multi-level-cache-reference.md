# Multi-level cache (L1 Caffeine + L2 Redis)

**Modules:** `CaffeineCacheModule` + `LettuceRedisCacheModule`
**Artifacts:** `io.koraframework:cache-caffeine` + `io.koraframework:cache-redis-lettuce`
**Mechanism:** repeat the same cache annotation once per level

---

## Contents

- [How stacking works](#how-stacking-works)
- [Setup](#setup)
- [Configuration](#configuration)
- [Reads](#reads)
- [Writes and invalidation](#writes-and-invalidation)
- [Rules the processor enforces](#rules-the-processor-enforces)
- [Different key types per level](#different-key-types-per-level)
- [Building a facade in code](#building-a-facade-in-code)
- [Operational considerations](#operational-considerations)
- [Troubleshooting](#troubleshooting)

---

## How stacking works

There is no dedicated "multi-level cache" type. The cache annotations are `@Repeatable`, and the
generated aspect walks the caches **in the order the annotations are written**:

```
@Cacheable(L1)   →  L1.get(key)   hit → return
@Cacheable(L2)   →  L2.get(key)   hit → L1.put(key, value); return
                    miss          →  run the method body
                                     L1.put(key, result); L2.put(key, result)
                                     return result
```

The back-fill is real: on an L2 hit the aspect writes the value into every **earlier** cache before
returning. With three levels an L3 hit populates L1 and L2.

Note the single-cache optimisation does not apply here: with one `SYNC` cache the aspect uses
`computeIfAbsent`, but with two or more it is `get` → body → `put`, so two concurrent misses can
both run the body.

---

## Setup

```groovy
dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")
    annotationProcessor "io.koraframework:annotation-processors"

    implementation "io.koraframework:cache-caffeine"
    implementation "io.koraframework:cache-redis-lettuce"
    implementation "io.koraframework:json-common"
    implementation "io.koraframework:config-hocon"
}
```

```java
@KoraApp
public interface Application extends
        HoconConfigModule,
        JsonModule,
        LogbackModule,
        CaffeineCacheModule,
        LettuceRedisCacheModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

Two `@Cache` interfaces with **distinct config paths** — one contract each:

```java
@Cache("cache.caffeine.users")
public interface UserCaffeineCache extends CaffeineCache<String, UserResponse> {}

@Cache("cache.redis.users")
public interface UserRedisCache extends RedisCache<String, @Json UserResponse> {}
```

```kotlin
@Cache("cache.caffeine.users")
interface UserCaffeineCache : CaffeineCache<String, UserResponse>

@Cache("cache.redis.users")
interface UserRedisCache : RedisCache<String, @Json UserResponse>
```

A single interface cannot extend both contracts:
`@Cache interface '…' implements both Redis and Caffeine cache contracts.`

---

## Configuration

```hocon
cache.caffeine.users {
  maximumSize      = 1000
  expireAfterWrite = "10m"
}

cache.redis.users {
  keyPrefix        = "users-"
  expireAfterWrite = "30m"
}

lettuce {
  uri            = "redis://localhost:6379"
  uri            = ${?REDIS_URL}
  user           = ${?REDIS_USER}
  password       = ${?REDIS_PASS}
  socketTimeout  = 15s
  commandTimeout = 15s
}
```

Give L1 the shorter TTL: it is the level that cannot be invalidated from another pod, so its TTL is
the upper bound on how long this pod may serve data another pod has already changed.

---

## Reads

```java
@Component
public class UserService {

    private final UserRepository userRepository;
    private final UserCaffeineCache userCaffeineCache;
    private final UserRedisCache userRedisCache;

    public UserService(UserRepository userRepository,
                       UserCaffeineCache userCaffeineCache,
                       UserRedisCache userRedisCache) {
        this.userRepository = userRepository;
        this.userCaffeineCache = userCaffeineCache;
        this.userRedisCache = userRedisCache;
    }

    @Cacheable(UserCaffeineCache.class)   // L1
    @Cacheable(UserRedisCache.class)      // L2
    public Optional<UserResponse> getUser(String id) {
        return userRepository.findById(id);
    }
}
```

```kotlin
@Component
open class UserService(
    private val userRepository: UserRepository,
    private val userCaffeineCache: UserCaffeineCache,
    private val userRedisCache: UserRedisCache
) {

    @Cacheable(UserCaffeineCache::class)
    @Cacheable(UserRedisCache::class)
    open fun getUser(id: String): UserResponse? = userRepository.findById(id)
}
```

The caches only need to be injected when you also use them imperatively — the aspect gets its own
references through the generated proxy constructor.

---

## Writes and invalidation

Repeat the write annotation for every level, in the same order:

```java
@CachePut(value = UserCaffeineCache.class, args = "id")
@CachePut(value = UserRedisCache.class, args = "id")
public UserResponse updateUser(String id, UserRequest request) { … }

@CacheInvalidate(UserCaffeineCache.class)
@CacheInvalidate(UserRedisCache.class)
public void deleteUser(String id) { … }

@CacheInvalidateAll(UserCaffeineCache.class)
@CacheInvalidateAll(UserRedisCache.class)
public void purge() { … }
```

`@CacheInvalidateAll` replaces the Kora 1.x `invalidateAll = true` attribute, which no longer
exists. On the Redis level it is `SCAN` + `DEL` over `keyPrefix` — and `FLUSHALL` if `keyPrefix` is
blank, so never leave it blank on a shared instance.

An eviction only clears **this pod's** L1. Other pods keep their own L1 copies until their TTL
expires. If that is unacceptable, either drop L1 for that entity or keep its `expireAfterWrite`
short enough to bound the staleness.

`mode = CacheMode.ASYNC` is a reasonable choice on the Redis level of a write path — it keeps the
network round-trip off the request thread. It is ignored on the Caffeine level (with a compile
warning), so declare it only where it means something.

---

## Rules the processor enforces

- Repeated annotations of one operation must use the **same** `args`, or:
  `Cache annotations on '…' use different key argument lists.`
- Operation kinds may not be mixed on one method:
  `Cache method '…' mixes different cache operation annotation types.`
  Use one method per operation.
- `@CacheInvalidate` and `@CacheInvalidateAll` may not share a method.

---

## Different key types per level

The levels do not have to share a key type. The aspect computes one key per distinct key type and
reuses it where the types are compatible — an L1 keyed by a record and an L2 keyed by `String` each
get their own key expression. What must match is the **value** type, since the same value flows into
every level.

---

## Building a facade in code

`Cache<K, V>` can also compose levels without annotations:

```java
Cache<String, UserResponse> layered = Cache.builder(userCaffeineCache)
        .addCache(userRedisCache)
        .build();
```

The facade reads levels in order, back-fills shallower levels on a hit, and fans `put`,
`invalidate` and `invalidateAll` out to all of them. One caveat: `get(Collection<K>)` on the facade
throws `UnsupportedOperationException` — use `computeIfAbsent(Collection, Function)` for bulk
access. Prefer the annotations unless you need a `Cache<K, V>` value at runtime.

---

## Operational considerations

- **Memory.** L1 lives in every pod's heap. Size `maximumSize` against the pod's heap, not against
  the dataset.
- **Stampede.** Clearing L2 sends every pod to the origin at once. Stagger TTLs, or invalidate
  single keys rather than everything.
- **Redis failure.** The Redis cache swallows errors, so an outage silently degrades L2 to "always
  miss". L1 keeps working; the origin takes the extra load. Alert on cache telemetry.
- **Serialisation cost.** Every L2 hit deserialises JSON. If the value is large and the hit rate on
  L1 is high, the L2 level may cost more than it saves.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| L1 never populated on an L2 hit | annotation order is reversed — the shallowest cache must be listed first |
| `Cache annotations on '…' use different key argument lists.` | the repeated annotations disagree on `args` |
| `@Cache interface '…' implements both Redis and Caffeine cache contracts.` | declare two interfaces, one contract each |
| `No component found for dependency: … RedisCacheClient (no tags)` | `LettuceRedisCacheModule` is not connected |
| stale reads on one pod only | that pod's L1 — shorten `expireAfterWrite` on the Caffeine level |
| both levels miss although Redis has the key | key mapping differs between levels, or `keyPrefix` changed |

---

## See also

- [cacheable-reference.md](cacheable-reference.md) — repeatability and `CacheMode`
- [cache-caffeine-reference.md](cache-caffeine-reference.md) — L1 config
- [cache-redis-reference.md](cache-redis-reference.md) — L2 config and `keyPrefix`
- [imperative-cache-reference.md](imperative-cache-reference.md) — the facade and `Cache<K, V>` API
