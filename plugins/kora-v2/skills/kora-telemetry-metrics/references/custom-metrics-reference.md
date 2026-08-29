# Custom Business Metrics Reference (Kora 2.0)

Patterns for recording your own metrics in a Kora 2.0 service.

## Contents

- [Where custom meters sit relative to the telemetry gates](#gates)
- [Basic pattern](#basic-pattern)
- [Dynamic tags with meter caching](#dynamic-tags)
- [High-throughput caching with a composite key](#composite-key)
- [Complete example — payment service](#payment-example)
- [Recording without a lambda](#recording)
- [Naming conventions](#naming)

---

## Where custom meters sit relative to the telemetry gates { #gates }

Custom meters need **`MetricsModule` only**. They are registered directly on the injected
`MeterRegistry`, so no `telemetry.metrics.enabled` flag governs them — those flags gate Kora's own
component instrumentation.

| | Needs `MetricsModule` | Needs `telemetry.metrics.enabled = true` |
|---|---|---|
| `http.server.request.duration` and friends | yes | **yes** |
| your `Counter.builder("user.creation.total")` | yes | no |

Without `MetricsModule` there is no `MeterRegistry` component at all, and the graph fails to build
with `No component found for dependency` naming `io.micrometer.core.instrument.MeterRegistry` —
a loud, immediate failure, unlike the silent component-metrics case.

All imports below are `io.micrometer.core.instrument.*` for the meters and
`io.koraframework.common.annotation.Component` for the DI annotation. Kora 2.0 contracts are
synchronous: no `Mono`, `CompletionStage` or `suspend`.

---

## Basic pattern { #basic-pattern }

Register each meter **once**, in the constructor. `Counter.builder(...).register(registry)` is
idempotent per name+tags, but calling it on every request pays a `ConcurrentHashMap` lookup and
allocation you do not need.

Java:

```java
package com.example;

import io.koraframework.common.annotation.Component;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;

import java.time.Duration;
import java.util.concurrent.Callable;

@Component
public final class BusinessMetrics {

    private final Timer operationTimer;
    private final Counter successCounter;

    public BusinessMetrics(MeterRegistry meterRegistry) {
        this.operationTimer = Timer.builder("user.creation.duration")
                .description("Time taken to create users")
                .serviceLevelObjectives(
                        Duration.ofMillis(50),
                        Duration.ofMillis(100),
                        Duration.ofMillis(250),
                        Duration.ofMillis(500))
                .register(meterRegistry);

        this.successCounter = Counter.builder("user.creation.total")
                .description("Total users created")
                .register(meterRegistry);
    }

    public <T> T recordCreation(Callable<T> action) throws Exception {
        var result = this.operationTimer.recordCallable(action);
        this.successCounter.increment();
        return result;
    }
}
```

`Timer.recordCallable` declares `throws Exception`, so either propagate it (as above) or translate
it deliberately. Swallowing it inside the metrics helper hides business failures behind a metrics
class — never do that.

Kotlin:

```kotlin
package com.example

import io.koraframework.common.annotation.Component
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.MeterRegistry
import io.micrometer.core.instrument.Timer
import java.time.Duration
import java.util.concurrent.Callable

@Component
class BusinessMetrics(meterRegistry: MeterRegistry) {

    private val operationTimer: Timer = Timer.builder("user.creation.duration")
        .description("Time taken to create users")
        .serviceLevelObjectives(
            Duration.ofMillis(50),
            Duration.ofMillis(100),
            Duration.ofMillis(250),
            Duration.ofMillis(500)
        )
        .register(meterRegistry)

    private val successCounter: Counter = Counter.builder("user.creation.total")
        .description("Total users created")
        .register(meterRegistry)

    fun <T> recordCreation(action: Callable<T>): T {
        val result = operationTimer.recordCallable(action)
        successCounter.increment()
        return result
    }
}
```

Note the constructor parameter is **not** a `val` — the registry is only needed to build the meters.
Keep it as a property only if you register meters lazily.

---

## Dynamic tags with meter caching { #dynamic-tags }

**Problem:** a tag value that varies at runtime (email provider, payment method, tenant), where the
value set is bounded.

**Solution:** cache one meter instance per value with `ConcurrentHashMap.computeIfAbsent`. This is
exactly what Kora's own factories do — `DefaultHttpServerMetricsFactory` keeps a
`requestDurationCache` and an `activeRequestsCache` keyed by a record.

```java
package com.example;

import io.koraframework.common.annotation.Component;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;

import java.time.Duration;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;

@Component
public final class CachedMetricsService {

    private final MeterRegistry meterRegistry;
    private final Timer userCreationTimer;
    private final ConcurrentHashMap<String, Counter> userCreationCounters = new ConcurrentHashMap<>();

    public CachedMetricsService(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
        this.userCreationTimer = Timer.builder("user.creation.duration")
                .description("Time taken to create users")
                .serviceLevelObjectives(
                        Duration.ofMillis(50),
                        Duration.ofMillis(100),
                        Duration.ofMillis(250),
                        Duration.ofMillis(500))
                .register(meterRegistry);
    }

    public <T> T recordUserCreation(String email, Callable<T> action) throws Exception {
        var result = this.userCreationTimer.recordCallable(action);
        this.userCreationCounter(emailProvider(email)).increment();
        return result;
    }

    private Counter userCreationCounter(String emailProvider) {
        return this.userCreationCounters.computeIfAbsent(emailProvider, provider ->
                Counter.builder("user.creation.total")
                        .description("Total number of users created")
                        .tag("email.provider", provider)
                        .register(this.meterRegistry));
    }

    private static String emailProvider(String email) {
        int at = email.indexOf('@');
        if (at < 0 || at == email.length() - 1) {
            return "unknown";
        }
        return email.substring(at + 1).toLowerCase(Locale.ROOT);
    }
}
```

**Key points:**

1. The Timer has no dynamic tag, so it is registered once in the constructor.
2. The Counter cache uses `computeIfAbsent`, which is atomic — two threads racing on a new provider
   get the same `Counter`.
3. The tag value is *derived* from domain data, not taken raw.
4. There is a bounded fallback (`"unknown"`) so malformed input cannot create a series.

Point 4 matters more than it looks: `email.substring(at + 1)` is only bounded if your registration
flow validates domains. If it does not, this cache is a memory leak with extra steps — map to a
known set instead (see [metrics-cardinality-reference.md](metrics-cardinality-reference.md)).

---

## High-throughput caching with a composite key { #composite-key }

When several tags vary together, key the cache on an immutable record rather than concatenating
strings.

```java
package com.example;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

public final class CachedMetrics {

    record MetricKey(String operation, String status) {}

    private final ConcurrentHashMap<MetricKey, Timer> timers = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<MetricKey, Counter> counters = new ConcurrentHashMap<>();
    private final MeterRegistry registry;

    public CachedMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    public void recordOperation(String operation, long durationNanos, boolean success) {
        var key = new MetricKey(operation, success ? "success" : "failed");
        var timer = this.timers.computeIfAbsent(key, k ->
                Timer.builder("custom.operation.duration")
                        .tag("operation", k.operation())
                        .tag("status", k.status())
                        .register(this.registry));
        timer.record(durationNanos, TimeUnit.NANOSECONDS);
    }

    public void incrementCounter(String operation, String type) {
        var key = new MetricKey(operation, type);
        var counter = this.counters.computeIfAbsent(key, k ->
                Counter.builder("custom.operation.count")
                        .tag("operation", k.operation())
                        .tag("type", k.type())
                        .register(this.registry));
        counter.increment();
    }
}
```

Kora's own factories carry this comment above every builder method:

```
// DO NOT ADD DYNAMIC TAGS IN BUILDER, use metric key instead of metric collision will happen
```

The reason: the cache key must contain **every** tag that varies. If it does not, two different tag
sets map to the same cache entry and the first-registered meter silently wins, so later calls record
under the wrong labels.

**When to cache:** hot paths, and any meter with more than one varying tag.
**When not to:** a plain untagged counter, and anything whose tag values are unbounded — caching an
unbounded tag just moves the leak into your own map.

---

## Complete example — payment service { #payment-example }

```java
package com.example;

import io.koraframework.common.annotation.Component;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.DistributionSummary;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;

import java.time.Duration;

@Component
public final class PaymentMetricsService {

    private final Counter paymentsCounter;
    private final Counter failuresCounter;
    private final Timer paymentTimer;
    private final DistributionSummary paymentAmount;

    public PaymentMetricsService(MeterRegistry registry) {
        this.paymentsCounter = registry.counter("payment.processed.total", "status", "success");
        this.failuresCounter = registry.counter("payment.processed.total", "status", "failed");

        this.paymentTimer = Timer.builder("payment.processing.duration")
                .description("Time taken to process payment")
                .serviceLevelObjectives(
                        Duration.ofMillis(50),
                        Duration.ofMillis(100),
                        Duration.ofMillis(500),
                        Duration.ofSeconds(1),
                        Duration.ofSeconds(5))
                .register(registry);

        this.paymentAmount = DistributionSummary.builder("payment.amount")
                .description("Payment amount distribution")
                .tag("currency", "RUB")
                .baseUnit("rubles")
                .register(registry);
    }

    public PaymentResult process(Payment payment) {
        return this.paymentTimer.record(() -> {
            try {
                var result = executePayment(payment);
                this.paymentsCounter.increment();
                this.paymentAmount.record(payment.amount());
                return result;
            } catch (PaymentException e) {
                this.failuresCounter.increment();
                throw e;
            }
        });
    }

    private PaymentResult executePayment(Payment payment) {
        return new PaymentResult(true, "Payment processed successfully");
    }

    public record Payment(double amount, String currency) {}

    public record PaymentResult(boolean success, String message) {}

    public static final class PaymentException extends RuntimeException {
        public PaymentException(String message) {
            super(message);
        }
    }
}
```

Two success/failure counters sharing one metric name with different `status` tags is the idiomatic
shape: a single Prometheus series family you can ratio without joining two metrics.

`Timer.record(Supplier<T>)` (the lambda above) only propagates unchecked exceptions — which is why
`PaymentException extends RuntimeException`. For a checked exception, use `recordCallable`.

---

## Recording without a lambda { #recording }

Sometimes the measured region is not a single call:

```java
var sample = Timer.start(registry);
try {
    doWork();
} finally {
    sample.stop(this.paymentTimer);
}
```

Or record a duration you already have:

```java
this.paymentTimer.record(elapsedNanos, TimeUnit.NANOSECONDS);
```

Prefer the lambda forms where they fit — they cannot leak an unstopped sample.

---

## Naming conventions { #naming }

Match Kora's own meters so dashboards read consistently:

| Pattern | Example | Type |
|---|---|---|
| `<noun>.<action>.duration` | `user.creation.duration` | Timer |
| `<noun>.<action>.total` | `user.creation.total` | Counter |
| `<noun>.<attribute>` | `payment.amount` | DistributionSummary with `baseUnit(...)` |
| `<system>.<component>.<metric>` | `http.server.request.duration` | Timer |

- dots as separators — Micrometer converts them for the Prometheus exposition format;
- `.duration` for latencies, `.total` for monotonic counts;
- set `baseUnit(...)` rather than encoding the unit only in prose;
- never put a unit in the name *and* a conflicting `baseUnit` — the exported suffix comes from
  `baseUnit`.

---

## References

- [micrometer-types-reference.md](micrometer-types-reference.md) — choosing Counter / Gauge / Timer / DistributionSummary
- [metrics-cardinality-reference.md](metrics-cardinality-reference.md) — keeping tag values bounded
- [metrics-config-reference.md](metrics-config-reference.md) — the two gates and `PrometheusMeterRegistryInitializer`
- [Micrometer concepts](https://docs.micrometer.io/micrometer/reference/concepts.html)
