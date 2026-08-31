# @RateLimited Reference

**Annotation:** `@RateLimited(X.class)` — `io.koraframework.resilient.ratelimiter.annotation.RateLimited`
**Spec:** `@RateLimiterSpec("<config path>")` on `interface X extends io.koraframework.resilient.ratelimiter.RateLimiter`
**Artifact:** `io.koraframework:resilient-kora`

> **New in Kora 2.0.** There is no 1.x equivalent to port.

## Contents

- [Basic Usage](#basic-usage)
- [Configuration](#configuration)
- [Semantics](#semantics)
- [Supported Return Types](#supported-return-types)
- [Imperative Use](#imperative-use)
- [Telemetry](#telemetry)
- [Common Pitfalls](#common-pitfalls)

---

## Basic Usage

```java
@RateLimiterSpec("resilient.ratelimiter.notifications")
public interface NotificationRateLimiter extends RateLimiter {}
```

```java
@Component
public class NotificationSender {                // NOT final

    private final SmsGateway gateway;

    public NotificationSender(SmsGateway gateway) {
        this.gateway = gateway;
    }

    @RateLimited(NotificationRateLimiter.class)
    public void send(Notification notification) {
        gateway.send(notification);
    }
}
```

```kotlin
@RateLimiterSpec("resilient.ratelimiter.notifications")
interface NotificationRateLimiter : RateLimiter
```

```kotlin
@Component
open class NotificationSender(private val gateway: SmsGateway) {

    @RateLimited(NotificationRateLimiter::class)
    open fun send(notification: Notification) = gateway.send(notification)
}
```

One spec per limit. Every method annotated with the same spec class draws from the **same** permit
pool — which is how you enforce one shared quota across several call sites. Two spec interfaces
pointing at the same config path are two independent pools, each with the full allowance.

---

## Configuration

```hocon
resilient.ratelimiter.notifications {
  limitForPeriod = 100            # required — permits granted per period
  limitRefreshPeriod = "1s"       # required — how often the pool is refilled
  enabled = true                  # optional, default true
}
```

Both `limitForPeriod` and `limitRefreshPeriod` are required and have no defaults; a missing one
fails at startup with
`ConfigValueException: Config expected value, but got null at path: 'ROOT.resilient.ratelimiter.notifications.limitForPeriod'`.

With `enabled = false` every `tryAcquire()` returns `true` and no permits are tracked.

---

## Semantics

`KoraRateLimiter` is a **fixed-window** counter, not a token bucket and not a leaky bucket:

- The pool starts full with `limitForPeriod` permits and a deadline `limitRefreshPeriod` ahead.
- Each call decrements the counter. At zero, `acquire()` throws
  `RateLimitExceededException("RateLimiter 'X' rate limit exceeded")`, where `X` is the spec
  interface's **simple name**.
- When the deadline passes, the next call resets the counter to `limitForPeriod` and moves the
  deadline forward. Refill is lazy — it happens on a call, not on a timer.
- **Callers never wait.** `acquire()` fails fast; there is no blocking or queueing variant. If you
  need to smooth traffic rather than reject it, pair `@RateLimited` with `@Retryable` (outermost) so
  a rejected call is retried after a delay.
- Unused permits do not carry over into the next window, and the window boundary is not aligned to
  the wall clock — a burst can straddle a boundary and briefly deliver up to `2 * limitForPeriod`
  calls, which is normal for a fixed window.

The aspect wraps the body as `acquire()` → body, so a rejected call never reaches the method.

---

## Supported Return Types

`@RateLimited` is the most restrictive of the five aspects:

| Return type | Java | Kotlin |
|---|---|---|
| `T`, `T?`, `void` / `Unit` | yes | yes |
| `Flow<T>` | n/a | yes — the permit is taken when collection starts |
| `suspend fun` | n/a | yes — the synchronous body is emitted into a `suspend` helper; `acquire()` never blocks, so there is no coroutine-specific path |
| `CompletionStage<T>` / `CompletableFuture<T>` | **no** | **no** |
| `Future<T>`, `Mono<T>`, `Flux<T>` | no | no |

`CompletionStage` is the one shape the other four Java aspects accept and `@RateLimited` does not.

Unsupported shapes fail the build with
`@RateLimited cannot be applied to '<Class>#<method>()' because return type '…' is not supported by this aspect.`

---

## Imperative Use

Inject the spec interface — it is a graph component:

```java
@Component
public final class BulkSender {

    private final NotificationRateLimiter rateLimiter;

    public BulkSender(NotificationRateLimiter rateLimiter) {
        this.rateLimiter = rateLimiter;
    }

    public void sendAll(List<Notification> batch) {
        for (var notification : batch) {
            if (!rateLimiter.tryAcquire()) {   // no exception, just a boolean
                deferred.add(notification);
                continue;
            }
            gateway.send(notification);
        }
    }
}
```

`RateLimiter` offers `tryAcquire()` (boolean), `acquire()` (throws), and
`execute(ThrowableRunnable)` / `execute(ThrowableCallable)`.

---

## Telemetry

Off by default. One counter, tagged `name` = the config path given to `@RateLimiterSpec`:

| Metric | Meaning |
|---|---|
| `resilient.ratelimiter.acquire` | one increment per acquire attempt, recording whether a permit was granted |

```hocon
resilient.telemetry.rateLimiter {
  logging.enabled = true
  metrics.enabled = true
}
```

---

## Common Pitfalls

| Problem | Cause / fix |
|---|---|
| `must extend io.koraframework.resilient.ratelimiter.RateLimiter` | Spec interface missing its base type. |
| Aspect rejects a `CompletionStage` method | `@RateLimited` is the only aspect that refuses it in Java; use a synchronous signature. |
| Two call sites each get the full quota | Duplicate spec interfaces on one config path — share a single spec. |
| Callers block instead of failing | They do not; `acquire()` fails fast. Add `@Retryable` outside `@RateLimited` if you want waiting. |
| Twice the expected rate at a window edge | Inherent to a fixed window; halve `limitRefreshPeriod` (and `limitForPeriod`) to tighten it. |
| Limit not applied across instances | This is a per-process limiter with no shared state. A cluster-wide quota needs external coordination. |

---

## See Also

- [retry-reference.md](retry-reference.md) — retrying a rejected call
- [resilience-config-reference.md](resilience-config-reference.md) — full `resilient.*` key set
