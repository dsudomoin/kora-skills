# Cache operation annotations

**Package:** `io.koraframework.cache.annotation`
**Artifact:** `io.koraframework:cache-common` (pulled in by `cache-caffeine` / `cache-redis-lettuce`)
**Processor:** `io.koraframework:annotation-processors` (Java) · `io.koraframework:symbol-processors` (KSP)

---

## Contents

- [Declared shapes](#declared-shapes)
- [@Cacheable](#cacheable)
- [@CachePut](#cacheput)
- [@CacheInvalidate](#cacheinvalidate)
- [@CacheInvalidateAll](#cacheinvalidateall)
- [The args attribute](#the-args-attribute)
- [CacheMode](#cachemode)
- [Supported return types](#supported-return-types)
- [Optional values](#optional-values)
- [Repeating annotations](#repeating-annotations)
- [What the processor generates](#what-the-processor-generates)
- [Compile-time diagnostics](#compile-time-diagnostics)

---

## Declared shapes

```java
@Repeatable(Cacheables.class) @Target(METHOD) @Retention(CLASS) @AopAnnotation
public @interface Cacheable {
    Class<? extends Cache<?, ?>> value();
    String[] args() default {};
    CacheMode mode() default CacheMode.SYNC;
}

@Repeatable(CachePuts.class)            // same three attributes
public @interface CachePut { … }

@Repeatable(CacheInvalidates.class)     // same three attributes
public @interface CacheInvalidate { … }

@Repeatable(CacheInvalidateAlls.class)  // value + mode only — no args
public @interface CacheInvalidateAll {
    Class<? extends Cache<?, ?>> value();
    CacheMode mode() default CacheMode.SYNC;
}

public enum CacheMode { SYNC, ASYNC }
```

There is **no** `invalidateAll` attribute anywhere in 2.0, and **no** `condition` / `unless` /
`keyGenerator` / SpEL support. The containers `@Cacheables`, `@CachePuts`, `@CacheInvalidates`,
`@CacheInvalidateAlls` exist only so the annotations can repeat; write them out only when a tool
forces you to.

---

## @Cacheable

Read-through. Look the key up; on a hit the annotated body is never executed.

```java
@Cacheable(OrderCache.class)
public OrderDto get(UUID id) {
    return repository.find(id);
}
```

For a single `SYNC` cache the generated body collapses to one atomic call:

```java
var _key = id;
return _cache.computeIfAbsent(_key, _k -> super.get(id));
```

so the loader runs under the cache's own compute lock (Caffeine `Cache#get(key, mappingFunction)`).
With more than one cache, or in `ASYNC` mode, it degrades to `get` → body → `put`.

Kotlin:

```kotlin
@Cacheable(OrderCache::class)
open fun get(id: UUID): OrderDto = repository.find(id)
```

---

## @CachePut

Write-through. The body always runs; its return value is written to the cache.

```java
@CachePut(value = OrderCache.class, args = "id")
public OrderDto update(UUID id, OrderRequest request) {
    return repository.save(id, request);
}
```

```kotlin
@CachePut(value = OrderCache::class, args = ["id"])
open fun update(id: UUID, request: OrderRequest): OrderDto = repository.save(id, request)
```

Without `args` the key would be built from **both** parameters (`id` *and* `request`), which is
almost never what you want on an update method — name the key parameters explicitly.

---

## @CacheInvalidate

The body always runs, then `cache.invalidate(key)`.

```java
@CacheInvalidate(OrderCache.class)
public void delete(UUID id) {
    repository.delete(id);
}
```

`void` / `Unit` is allowed here (unlike `@Cacheable` / `@CachePut`). A non-void method keeps its
return value; the eviction happens after the body and before the `return`.

---

## @CacheInvalidateAll

Replaces Kora 1.x `@CacheInvalidate(value = X.class, invalidateAll = true)`.

```java
@CacheInvalidateAll(OrderCache.class)
public void deleteAll() {
    repository.deleteAll();
}
```

```kotlin
@CacheInvalidateAll(OrderCache::class)
open fun deleteAll() = repository.deleteAll()
```

It takes no key, so the method needs no parameters. On a `RedisCache` this is `SCAN` by
`keyPrefix` followed by `DEL`; **with a blank `keyPrefix` it degrades to `FLUSHALL`** and wipes the
whole Redis database — see [cache-redis-reference.md](cache-redis-reference.md).

Do not put `@CacheInvalidate` and `@CacheInvalidateAll` on the same method:

```
Cache operation annotations on '[class=OrderService, method=purge]' mix @CacheInvalidate and @CacheInvalidateAll.

Fix: use either key-based invalidation annotations or invalidate-all annotations on the same method, not both.
```

---

## The args attribute

`String[] args()` names the **method parameters** that make up the key.

| `args` | Key |
|---|---|
| omitted / `{}` | every method parameter, in declaration order |
| `"id"` | just `id` |
| `{ "orderId", "tenantId" }` | `orderId` then `tenantId` — reordering and subsetting in one step |

```java
@Cacheable(value = OrderCache.class, args = { "orderId", "tenantId" })
public OrderDto find(UUID tenantId, String trace, UUID orderId) { … }
// key = new OrderCache.Key(orderId, tenantId); `trace` is ignored
```

A name that is not a parameter of the method is a compile error:

```
Cache key references unknown method parameter 'orderid'.

Available parameters on 'find': [tenantId, trace, orderId].
Fix: update annotation args to use existing method parameter names.
```

Limits: at most 9 key arguments without a custom mapper; `@Cacheable` / `@CacheInvalidate` need at
least one.

```
@Cacheable on 'get' requires at least one Cache Key method argument, but got 0.

Fix: add a method parameter used as the key, specify args explicitly, or use @CacheInvalidateAll for invalidate-all behavior.
```

Kotlin note: `args` is an array literal — `args = ["id", "traceId"]`.

---

## CacheMode

```java
@CachePut(value = OrderRedisCache.class, args = "id", mode = CacheMode.ASYNC)
public OrderDto update(UUID id, OrderRequest request) { … }
```

- `SYNC` (default): `put` / `invalidate` / `invalidateAll` run on the calling thread.
- `ASYNC`: the write is submitted to an `Executor` requested as
  `@Tag(CacheMode.class) Executor`. `CacheCommonModule` supplies a `@DefaultComponent`
  implementation that starts one virtual thread per operation, named `kora-cache-0`, `kora-cache-1`,
  …, with an uncaught-exception handler that logs
  `Cache asynchronous operation failed on thread {}` at WARN.

Consequences worth stating out loud:

- Only the write side is asynchronous. The lookup in `@Cacheable` is always synchronous.
- A failed asynchronous write is logged and dropped — the caller never sees it.
- `ASYNC` on a Caffeine cache is silently downgraded to `SYNC`, with a compile warning:
  `Cache async mode is ignored for CaffeineCache com.example.OrderCache`.
- Replace the executor with your own `@Component @Tag(CacheMode.class) Executor` bean if you need a
  bounded pool.

---

## Supported return types

Cache AOP in 2.0 is **synchronous only**.

| Return type | `@Cacheable` / `@CachePut` | `@CacheInvalidate` / `@CacheInvalidateAll` |
|---|---|---|
| `T`, `@Nullable T` (Java) / `T`, `T?` (Kotlin) | yes | yes |
| `Optional<T>` | yes — see below | yes |
| primitives | yes | yes |
| `void` / `Unit` | **no** | yes |
| `CompletionStage` / `CompletableFuture` | **no** | **no** |
| `Publisher` / `Mono` / `Flux` | **no** | **no** |
| Kotlin `suspend` | **no** | **no** |

Rejections read:

```
@Cacheable cannot be applied to 'OrderService#get()' because the method returns void.

Fix: cache get/put methods must return the value that should be read from or written to cache.
```

```
@Cacheable cannot be applied to 'OrderService#get()' because return type 'java.util.concurrent.CompletionStage<OrderDto>' is not supported by the Java cache AOP aspect.

Fix: use a synchronous method return type supported by cache AOP. Cache get/put methods must return a value; invalidate methods may return void.
```

---

## Optional values

An `Optional<T>` return works two ways, and the difference matters:

- `Cache<K, T>` + `Optional<T>` method — only present values are cached; an empty `Optional` is not
  stored, so the next call re-executes the body.
- `Cache<K, Optional<T>>` + `Optional<T>` method — the empty `Optional` **is** stored, which is how
  you cache a negative lookup.

The `migration/2.0` guide apps use the first form (`Cacheable` over
`CaffeineCache<String, UserResponse>` on `Optional<UserResponse> getUser(String id)`); the Kotlin
twin uses a nullable return (`UserResponse?`) instead.

---

## Repeating annotations

Repeating the same operation across caches is how multi-level caching is expressed:

```java
@Cacheable(UserCaffeineCache.class)
@Cacheable(UserRedisCache.class)
public Optional<UserResponse> getUser(String id) { … }
```

Rules enforced at compile time:

- All repeats of one operation must use the **same** `args`:

  ```
  Cache annotations on '[class=UserService, method=getUser]' use different key argument lists.

  Expected same args for every cache annotation in one operation, got [id] and [id, tenant].
  Fix: make all repeated cache annotations use identical args.
  ```

- Different operation kinds may not be mixed on one method:

  ```
  Cache method '[class=UserService, method=getUser]' mixes different cache operation annotation types.

  Fix: use only one operation kind per method: @Cacheable, @CachePut, @CacheInvalidate, or @CacheInvalidateAll.
  ```

---

## What the processor generates

For `@Cache("orders.cache") interface OrderCache extends CaffeineCache<UUID, OrderDto>`:

| Generated type | Role |
|---|---|
| `$OrderCache_Impl` | subclass of `AbstractCaffeineCache` / `AbstractRedisCache` bound to the config path |
| `$OrderCache_Module` | `@Module` interface providing `OrderCache` and its `@Tag(OrderCache.class)` config |

For a class carrying cache annotations, `$OrderService__AopProxy` is generated, and it is the proxy
— not your class — that the graph instantiates. Caches and `@Mapping` mappers become constructor
parameters of that proxy.

Both generated modules are discovered automatically: `@KoraApp` collects every `@Module` interface
in the compilation. Never list `$OrderCache_Module` on `@KoraApp` by hand, and never edit anything
under `build/generated`.

---

## Compile-time diagnostics

| Message (first line) | Cause |
|---|---|
| `@Cache can only be applied to an interface, but '…' is CLASS.` | `@Cache` on a class |
| `@Cache interface '…' does not implement a supported cache contract.` | interface extends neither `CaffeineCache` nor `RedisCache` |
| `@Cache interface '…' implements both Redis and Caffeine cache contracts.` | pick one |
| `@Cache config path '…' has invalid format.` | path must contain a letter-led identifier |
| `AOP aspect cannot be applied to class '…' because the class is final.` | Java `final` target |
| `AOP aspect cannot be applied to class '…' because the class is not open.` | Kotlin non-`open` class |
| `AOP aspect cannot be applied to function '…' because the function is not open.` | Kotlin non-`open` function |
| `AOP aspect cannot be applied to method '…' because the method is private.` | private target |
| `AOP proxy cannot be generated for '…': no suitable constructor was found.` | no accessible constructor |
| `@Cacheable does not support more than 9 method arguments for Cache Key, but '…' uses 12.` | supply a `CacheKeyMapper` via `@Mapping` |

---

## See also

- [cache-key-mapper-reference.md](cache-key-mapper-reference.md) — keys and `@Mapping`
- [cache-caffeine-reference.md](cache-caffeine-reference.md) — Caffeine setup and config
- [cache-redis-reference.md](cache-redis-reference.md) — Redis setup and config
- [multi-level-cache-reference.md](multi-level-cache-reference.md) — L1/L2 stacking
- [imperative-cache-reference.md](imperative-cache-reference.md) — the `Cache<K, V>` API
