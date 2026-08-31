# @Retryable Reference

**Annotation:** `@Retryable(X.class)` — `io.koraframework.resilient.retry.annotation.Retryable`
**Spec:** `@RetrySpec("<config path>")` on `interface X extends io.koraframework.resilient.retry.Retry`
**Artifact:** `io.koraframework:resilient-kora`

> Renamed in 2.0. The 1.x annotation was `@Retry("name")`; `Retry` is now the runtime contract that
> spec interfaces extend.

## Contents

- [Basic Usage](#basic-usage)
- [Attempt Counting](#attempt-counting)
- [Configuration](#configuration)
- [Delay Strategies](#delay-strategies)
- [Retry Budget](#retry-budget)
- [Failure Predicate](#failure-predicate)
- [Imperative Use](#imperative-use)
- [Telemetry](#telemetry)
- [Common Pitfalls](#common-pitfalls)

---

## Basic Usage

```java
@RetrySpec("resilient.retry.user")
public interface UserRetry extends Retry {}
```

```java
@Component
public class UserService {                       // NOT final

    private final UserRepository repository;

    public UserService(UserRepository repository) {
        this.repository = repository;
    }

    @Retryable(UserRetry.class)
    public Optional<User> findById(String id) {
        return repository.findById(id);
    }
}
```

```kotlin
@RetrySpec("resilient.retry.user")
interface UserRetry : Retry
```

```kotlin
@Component
open class UserService(private val repository: UserRepository) {

    @Retryable(UserRetry::class)
    open fun findById(id: String): User? = repository.findById(id)
}
```

One spec per logical configuration name, reused by every method that shares it. When a retry budget
is configured, sharing a spec also means sharing that budget — which is usually what you want.

---

## Attempt Counting

`attempts` is the number of **retries**, not the total number of invocations.

| `attempts` | Invocations on persistent failure | Outcome |
|---|---|---|
| `0` | 1 | the original exception propagates unchanged |
| `2` | 3 | `RetryExhaustedException` |
| `3` | 4 | `RetryExhaustedException` |

`RetryExhaustedException` wraps the last failure as its cause and reads
`All 'N' retry attempts exhausted: <cause message>`. With `enabled = false` or `attempts = 0` the
aspect never wraps: the original exception surfaces as-is. Code that catches
`RetryExhaustedException` therefore stops working the moment someone sets `attempts = 0`.

---

## Configuration

```hocon
resilient.retry.user {
  delay = "100ms"      # required — base delay before the first retry
  attempts = 3         # required — number of retries after the initial call
  delayStep = "100ms"  # optional, default 0 — linear increment per attempt
  enabled = true       # optional, default true
}
```

Optional sub-blocks: `backoff`, `jitter`, `retryBudget` — see below. `delay` and `attempts` have no
defaults; a section missing either fails at startup with
`ConfigValueException: Config expected value, but got null at path: 'ROOT.resilient.retry.user.delay'`.

**Named sections do not inherit `resilient.retry.default`.** Each spec resolves exactly one path.

---

## Delay Strategies

### Linear (default)

Without a `backoff` block the delay before retry *n* is `delay + delayStep * (n - 1)`:

```hocon
resilient.retry.user { delay = "100ms", attempts = 4, delayStep = "200ms" }
# waits: 100ms, 300ms, 500ms, 700ms
```

With the default `delayStep = 0` every retry waits exactly `delay`.

### Exponential

Adding a `backoff` block **replaces** the linear formula with
`delay * multiplier^(n - 1)`, capped at `delayMax`. `delayStep` is ignored once `backoff` is set.

```hocon
resilient.retry.external {
  delay = "100ms"
  attempts = 5
  backoff {
    type = EXPONENTIAL   # the only value
    multiplier = 2.0     # default 2.0
    delayMax = "5s"      # optional cap
  }
}
# waits: 100ms, 200ms, 400ms, 800ms, 1600ms
```

### Jitter

Applied on top of whichever formula is active. `FULL` jitter picks uniformly from
`[computed - computed * ratio, computed]`, so it only ever *shortens* a wait:

```hocon
resilient.retry.external.jitter { type = FULL, ratio = 0.5 }
# a computed 800ms becomes a uniform pick from [400ms, 800ms]
```

`type = NONE` (default) disables it. `ratio` defaults to `1.0`, i.e. `[0, computed]`.

### Worst-case latency

Linear: `attempts * delay + delayStep * attempts * (attempts - 1) / 2`, plus the work itself.
Budget it against any enclosing `@Timeout` — an overall timeout smaller than the retry schedule
means the retries can never finish.

---

## Retry Budget

New in 2.0. A token bucket that caps the *proportion* of traffic that is retried, so a broad outage
cannot multiply load on a struggling dependency:

```hocon
resilient.retry.external.retryBudget {
  enabled = true            # default true
  ratio = 0.1               # default 0.1 — retries allowed per successful call
  tokensMax = 100           # default 100
  tokensInitial = 10        # default 10
  minTokensPerSecond = 0.0  # default 0.0
}
```

When the budget denies a retry the attempt is rejected: the original exception propagates instead of
`RetryExhaustedException`, and the `resilient.retry.exhausted` counter is tagged
`reason=EXHAUSTED_BUDGET` rather than `EXHAUSTED_ATTEMPTS`.

Omitting the `retryBudget` block leaves the budget off entirely.

---

## Failure Predicate

By default every `Throwable` is retried (`Retry.isFailure` returns `true`). Two ways to narrow it,
the second winning over the first — same shape as the circuit breaker.

### 1. Override `isFailure` on the spec

```java
@RetrySpec("resilient.retry.external")
public interface ExternalRetry extends Retry {

    @Override
    default boolean isFailure(Throwable throwable) {
        return throwable instanceof SocketTimeoutException
            || throwable instanceof ConnectException;
    }
}
```

### 2. A `@Tag`-bound `RetryPredicate` component

`RetryPredicate` is a `@FunctionalInterface` with the single method
`boolean isRetryFailure(Throwable)` — **the 1.x `name()` + `test(Throwable)` pair and the
`failurePredicateName` config key are both gone.**

```java
@Tag(ExternalRetry.class)
@Component
public final class TransientOnlyRetryPredicate implements RetryPredicate {

    @Override
    public boolean isRetryFailure(Throwable throwable) {
        return throwable instanceof SocketTimeoutException
            || throwable instanceof ConnectException;
    }
}
```

```kotlin
@Tag(ExternalRetry::class)
@Component
class TransientOnlyRetryPredicate : RetryPredicate {
    override fun isRetryFailure(throwable: Throwable): Boolean =
        throwable is SocketTimeoutException || throwable is ConnectException
}
```

A rejected exception ends the retry loop immediately and propagates unchanged — no
`RetryExhaustedException`. An untagged `RetryPredicate` component is ignored.

---

## Imperative Use

`RetryManager` does not exist in 2.0. Inject the spec interface — it is a graph component:

```java
@Component
public final class DataSyncService {

    private final SyncRetry retry;

    public DataSyncService(SyncRetry retry) {
        this.retry = retry;
    }

    public Data fetch() {
        return retry.retry(() -> dataSource.fetch());
    }
}
```

`Retry` offers `retry(ThrowableRunnable)`, `retry(ThrowableCallable)`,
`retry(callable, fallback)`, and `asState()` for driving the loop by hand
(`onException(t)` → `ACCEPTED`/`REJECTED`/`EXHAUSTED`, `doDelay()`, `getAttempts()`,
`getDelayNanos()`; it is `AutoCloseable` and must be closed to flush telemetry).

---

## Telemetry

Off by default. Counters, tagged `name` = the config path given to `@RetrySpec`:

| Metric | Meaning | Extra tags |
|---|---|---|
| `resilient.retry.attempts` | one increment per retry performed | — |
| `resilient.retry.exhausted` | retry loop gave up | `reason` = `EXHAUSTED_ATTEMPTS` / `EXHAUSTED_BUDGET` |

Enable under `resilient.telemetry.retry`, or per spec under `<specPath>.telemetry`.

---

## Common Pitfalls

| Problem | Cause / fix |
|---|---|
| `String cannot be converted to Class<? extends Retry>` | Un-migrated `@Retry("name")`. |
| `must extend io.koraframework.resilient.retry.Retry` | Spec interface missing its base type. |
| One fewer / one more call than expected | `attempts` counts retries, not total invocations. |
| `RetryExhaustedException` never thrown | `attempts = 0`, `enabled = false`, or the predicate rejected the exception — the original propagates. |
| `backoff` set but `delayStep` ignored | Expected: `backoff` replaces the linear formula. |
| Retries amplify an outage | Add a `retryBudget` block, or narrow the predicate. |
| Non-idempotent operation retried | Retry only idempotent work, or carry an idempotency key. |
| Retry blows the enclosing timeout | Put `@Timeout` *below* `@Retryable` for a per-attempt bound; see [timeout-reference.md](timeout-reference.md). |
| Predicate ignored | Missing `@Tag(<Spec>.class)`. |

---

## See Also

- [timeout-reference.md](timeout-reference.md) — per-attempt vs overall bounds
- [circuit-breaker-reference.md](circuit-breaker-reference.md) — stop retrying a dead dependency
- [resilience-config-reference.md](resilience-config-reference.md) — full `resilient.*` key set
