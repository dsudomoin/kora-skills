# Cache keys and `CacheKeyMapper`

**Contract:** `io.koraframework.cache.CacheKeyMapper<T, A1>` plus the nested
`CacheKeyMapper.CacheKeyMapper2<T, A1, A2>` … `CacheKeyMapper9<…>`
**Applied with:** `io.koraframework.common.annotation.Mapping`
**Redis extras:** `io.koraframework.cache.redis.mapper.{RedisCacheKeyMapper, RedisCacheValueMapper}`

---

## Contents

- [How the key is chosen](#how-the-key-is-chosen)
- [Single parameter](#single-parameter)
- [Composite key from a record or data class](#composite-key-from-a-record-or-data-class)
- [CacheKeyMapper with @Mapping](#cachekeymapper-with-mapping)
- [Mappers must be components](#mappers-must-be-components)
- [Tagging a mapper](#tagging-a-mapper)
- [Multi-argument mappers](#multi-argument-mappers)
- [Redis key serialisation](#redis-key-serialisation)
- [Choosing an approach](#choosing-an-approach)

---

## How the key is chosen

The processor walks these rules in order, using the parameters selected by `args` (or all of them
when `args` is empty):

1. `@Mapping(SomeMapper.class)` is present and `SomeMapper` implements one of the
   `CacheKeyMapper*` interfaces → inject it and call `map(...)`.
2. Exactly one parameter → use it directly as the key.
3. Operation is `@CacheInvalidateAll` → no key at all.
4. The cache key type has a **public constructor** whose parameter types match the selected
   parameters (exactly, then by subtyping) → emit `new Key(a, b, …)`.
5. Otherwise → inject a `CacheKeyMapper.CacheKeyMapperN<Key, A1, …, AN>` component and call it.

Rule 4 is why records (Java) and data classes (Kotlin) work with no extra code. Rule 5 is what
happens when they do not — and it needs a component you must declare yourself.

---

## Single parameter

```java
@Cache("orders.cache")
public interface OrderCache extends CaffeineCache<UUID, OrderDto> {}

@Cacheable(OrderCache.class)
public OrderDto get(UUID id) { … }        // key = id
```

The key type of the cache and the parameter type must agree — the generated aspect passes the
parameter straight through.

---

## Composite key from a record or data class

```java
@Cache("orders.cache")
public interface OrderCache extends CaffeineCache<OrderCache.Key, OrderDto> {
    record Key(UUID tenantId, UUID orderId) {}
}

@Cacheable(OrderCache.class)
public OrderDto get(UUID tenantId, UUID orderId) { … }   // key = new Key(tenantId, orderId)
```

```kotlin
@Cache("orders.cache")
interface OrderCache : CaffeineCache<OrderCache.Key, OrderDto> {
    data class Key(val tenantId: UUID, val orderId: UUID)
}

@Cacheable(OrderCache::class)
open fun get(tenantId: UUID, orderId: UUID): OrderDto = repository.find(tenantId, orderId)
```

Positional, so parameter order must match the constructor. Use `args` to reorder rather than
rewriting the method signature:

```java
@Cacheable(value = OrderCache.class, args = { "tenantId", "orderId" })
public OrderDto find(UUID orderId, String trace, UUID tenantId) { … }
```

The key type must have working `equals` / `hashCode` — records and data classes get them free; a
hand-written class must implement both.

---

## CacheKeyMapper with `@Mapping`

Use a mapper when the method takes a domain object rather than the key, or when the key needs
computing.

```java
@Root
@Component
public class OrderService {

    public record OrderContext(String tenantId, String orderId, String traceId) {}

    @Component
    public static final class OrderContextMapping implements CacheKeyMapper<OrderCache.Key, OrderContext> {

        @Override
        public OrderCache.Key map(OrderContext arg) {
            return new OrderCache.Key(arg.tenantId(), arg.orderId());
        }
    }

    @Mapping(OrderContextMapping.class)
    @Cacheable(OrderCache.class)
    public OrderDto getByContext(OrderContext context) { … }
}
```

```kotlin
@Root
@Component
open class OrderService {

    data class OrderContext(val tenantId: String, val orderId: String, val traceId: String)

    @Component
    class OrderContextMapping : CacheKeyMapper<OrderCache.Key, OrderContext> {
        override fun map(arg: OrderContext): OrderCache.Key = OrderCache.Key(arg.tenantId, arg.orderId)
    }

    @Mapping(OrderContextMapping::class)
    @Cacheable(OrderCache::class)
    open fun getByContext(context: OrderContext): OrderDto = repository.find(context.tenantId, context.orderId)
}
```

`CacheKeyMapper` extends `Mapping.MappingFunction`, so `@Mapping` accepts it. The mapper's `map`
must return a non-null key — the cache modules are `@NullMarked`, and a Redis composite key mapper
throws `NullPointerException` with
`Redis cache key '<Key>' field '<field>' must be non null after mapping` if a field maps to null.

Do **not** carry Java nullability annotations into these signatures: 2.0 uses JSpecify
(`org.jspecify.annotations.*`), not `jakarta.annotation.Nonnull`, and inside a `@NullMarked` module
the default is already non-null, so no annotation is needed at all.

---

## Mappers must be components

The cache processor injects the `@Mapping` class into the generated
`$Service__AopProxy` constructor **unconditionally**. It never constructs a dependency-free mapper
itself.

- **Always** annotate the mapper `@Component`, nested classes included.
- This differs from HTTP request/response mappers, where a dependency-free mapper is constructed by
  the generated module and adding `@Component` causes `Multiple components match dependency`. That
  hazard does not exist for cache key mappers: the dependency claim targets the concrete mapper
  class, so one `@Component` yields exactly one candidate.

Missing `@Component`:

```
No component found for dependency:
  com.example.OrderService.OrderContextMapping (no tags)

Required at:
  com.example.$OrderService__AopProxy
  parameter: com.example.OrderService.OrderContextMapping mapper1
…
Fix:
  - Add @Component to an implementation of com.example.OrderService.OrderContextMapping.
  …
```

Both migrated cache examples on `migration/2.0` declare the mapper exactly this way — a
`@Component public static final class` nested inside the `@Component` service.

---

## Tagging a mapper

When two mappers implement the same mapper type, disambiguate with `@Tag` on the method next to
`@Mapping`:

```java
@Tag(TenantKeyMapper.class)
@Mapping(TenantKeyMapper.class)
@Cacheable(OrderCache.class)
public OrderDto get(String tenant, BigDecimal amount) { … }
```

The tag is copied onto the injected constructor parameter of the AOP proxy.

---

## Multi-argument mappers

For two or more key parameters, implement the nested arity-specific interface:

```java
import io.koraframework.cache.CacheKeyMapper;

@Component
public static final class TenantOrderMapper
        implements CacheKeyMapper.CacheKeyMapper2<OrderCache.Key, UUID, UUID> {

    @Override
    public OrderCache.Key map(UUID tenantId, UUID orderId) {
        return new OrderCache.Key(tenantId, orderId);
    }
}

@Mapping(TenantOrderMapper.class)
@Cacheable(OrderCache.class)
public OrderDto get(UUID tenantId, UUID orderId) { … }
```

Arities 1 through 9 exist (`CacheKeyMapper`, then `CacheKeyMapper2` … `CacheKeyMapper9`). Beyond
nine parameters there is no built-in form:

```
@Cacheable does not support more than 9 method arguments for Cache Key, but 'get' uses 12.

Fix: provide a custom CacheKeyMapper with @Mapping, or reduce the cache key arguments to at most 9.
```

Wrap the extra parameters into one key object instead.

---

## Redis key serialisation

A `RedisCache` converts the key object to bytes with a `RedisCacheKeyMapper<K>`
(`Function<K, byte[]>`). `RedisCacheMapperModule` — mixed into `RedisCacheModule`, and therefore
into `LettuceRedisCacheModule` — ships `@DefaultComponent` mappers for `String`, `byte[]`,
`Boolean`, `Character`, `Short`, `Integer`, `Long`, `BigInteger`, `BigDecimal`, `UUID`, `Instant`,
`LocalDate`, `LocalDateTime`, `ZonedDateTime`, `Duration`, `Period`, any `Enum` and any
`Collection<T>` whose element type has a mapper.

For a **record** (Java) or **data class** (Kotlin) key type, the generated `$Cache_Module` adds a
composite `RedisCacheKeyMapper` that maps each component with its own mapper and joins them with
`:` (`RedisCacheKeyMapper.DELIMITER`). Nothing extra to write, as long as every component type has
a mapper.

For any other key type supply your own:

```java
@Component
public final class OrderKeyRedisMapper implements RedisCacheKeyMapper<OrderKey> {

    @Override
    public byte[] apply(OrderKey key) {
        return (key.tenantId() + ":" + key.orderId()).getBytes(StandardCharsets.UTF_8);
    }
}
```

Note the two different mapper concepts and keep them apart:

| Interface | Direction | Selected by |
|---|---|---|
| `CacheKeyMapper` | method arguments → cache key object | `@Mapping` on the method |
| `RedisCacheKeyMapper` | cache key object → Redis key bytes | key type of the `@Cache` interface |
| `RedisCacheValueMapper` | cache value ↔ Redis value bytes | value type (and its `@Json` tag) |

---

## Choosing an approach

| Situation | Approach |
|---|---|
| the argument *is* the key | nothing — rule 2 |
| several arguments that match a record / data class | declare the key type, rely on rule 4 |
| the method has extra arguments, or the order is wrong | `args = { … }` |
| the argument is a domain object | `CacheKeyMapper` + `@Mapping` |
| the key needs computing (hash, normalise, tenant prefix) | `CacheKeyMapper` + `@Mapping` |
| more than 9 key arguments | wrap into one key object |

---

## See also

- [cacheable-reference.md](cacheable-reference.md) — the operation annotations and `args`
- [cache-redis-reference.md](cache-redis-reference.md) — value mappers and `keyPrefix`
- [imperative-cache-reference.md](imperative-cache-reference.md) — using the cache without aspects
