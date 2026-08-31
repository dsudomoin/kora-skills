# JDK Scheduling Reference

**Artifact:** `io.koraframework:scheduling-jdk`
**Module:** `io.koraframework.scheduling.jdk.SchedulingJdkModule`
**Annotations:** `io.koraframework.scheduling.jdk.annotation.*`
**Runtime:** `java.util.concurrent.ScheduledThreadPoolExecutor`, in-process, non-persistent

## Contents

- [Annotations](#annotations) — `@ScheduleAtFixedRate`, `@ScheduleWithFixedDelay`, `@ScheduleOnce`, `@ScheduleWithCron`
- [What the processor generates](#what-the-processor-generates)
- [Method requirements](#method-requirements)
- [Cron syntax](#cron-syntax)
- [JDK vs Quartz](#jdk-vs-quartz)
- [Error handling](#error-handling)
- [Telemetry](#telemetry)

---

## Annotations

All four target `METHOD`, have `RetentionPolicy.CLASS`, and expose `String config() default ""`.

### `@ScheduleAtFixedRate`

| Attribute | Type | Default |
|---|---|---|
| `initialDelay` | `long` | `0` |
| `period` | `long` | `0` |
| `unit` | `ChronoUnit` | `MILLIS` |
| `config` | `String` | `""` |

The period is measured **start → start**. Backed by `ScheduledExecutorService.scheduleAtFixedRate`,
so if a run overruns the period the next run starts late; it is never started concurrently. Kora also
holds a per-job `ReentrantLock` for the whole run, which makes non-overlap structural rather than
incidental.

Use for a cadence you want to keep: heartbeat, metric scrape, health poll, cache refresh.

```java
@Component
public final class HeartbeatJob {

    @ScheduleAtFixedRate(initialDelay = 30, period = 60, unit = ChronoUnit.SECONDS)
    void heartbeat() { }
}
```

### `@ScheduleWithFixedDelay`

| Attribute | Type | Default |
|---|---|---|
| `initialDelay` | `long` | `0` |
| `delay` | `long` | `0` |
| `unit` | `ChronoUnit` | `MILLIS` |
| `config` | `String` | `""` |

The delay is measured **end → start** — a full `delay` of idle time after the previous run returned.
The cadence therefore drifts with execution time, which is what you want when the work is long or its
duration varies.

```java
@ScheduleWithFixedDelay(initialDelay = 30, delay = 120, unit = ChronoUnit.SECONDS)
void syncData() { }
```

### `@ScheduleOnce`

| Attribute | Type | Default |
|---|---|---|
| `delay` | `long` | `0` |
| `unit` | `ChronoUnit` | `MILLIS` |
| `config` | `String` | `""` |

One execution, `delay` after the graph starts the job. Use for cache warm-up, late binding, a one-shot
startup task that must not block graph initialisation.

```java
@ScheduleOnce(delay = 5, unit = ChronoUnit.MINUTES)
void warmup() { }
```

### `@ScheduleWithCron`

| Attribute | Type | Default |
|---|---|---|
| `value` | `String` | `""` |
| `config` | `String` | `""` |

New in Kora 2.0 — an in-process cron evaluator (`io.koraframework.scheduling.jdk.CronExpression`) with
no Quartz dependency. Each run schedules the next one from `ZonedDateTime.now()`, i.e. in the **JVM
default time zone**; set `-Duser.timezone` / `TZ` if you need a fixed one.

```java
@ScheduleWithCron("0 0 3 * * ?")
void nightlyCompaction() { }
```

The expression is parsed while the graph is built, so an invalid one fails **startup** with
`IllegalArgumentException` (`Cron expression must contain 5, 6 or 7 fields, got …`), not compilation.
If the expression can never fire again before the year 2100, the job logs a warning at WARN and stops
rescheduling instead of throwing.

---

## Required parameter rule

Every annotation needs either its primary attribute or a `config` path. A `period`/`delay` of `0` and a
blank cron `value` count as **unset**, and the build fails:

```
Either period() or config() annotation parameter must be provided
```

The Kotlin processor prints the long form:

```
Invalid `@ScheduleAtFixedRate` configuration on `com.example.Jobs.heartbeat`.

The annotation must define either `period` directly or `config` with a config path.
A zero or blank `period` is treated as missing.

Fix: set `period` on the annotation, or set `config` and provide scheduling settings in application config.
```

---

## What the processor generates

For every class holding scheduled methods the processor writes one `@Module` interface next to it:

```
$<ClassName>_SchedulingModule
```

with one `@Root` factory method per scheduled method:

```
$<ClassName>_<methodName>_Job
```

returning `FixedRateJob`, `FixedDelayJob`, `RunOnceJob` or `CronJob` from
`io.koraframework.scheduling.jdk`. The factory takes `SchedulingTelemetryFactory`,
`SchedulingJdkExecutor` and `ValueOf<YourClass>`, and the job body is a lambda
`() -> target.get().yourMethod()`.

Two things follow from that shape:

1. **No AOP proxy is created.** Your class is called directly through `ValueOf`, so it does not have to
   be non-`final` (Java) or `open` (Kotlin) for scheduling to work.
2. The job component is annotated `@Root`, so it is never pruned out of the graph even though nothing
   depends on it.

When the annotation carries `config = "<path>"` the processor additionally writes a
`@ConfigMapper` interface

```
$<ClassName>_<methodName>_Config extends SchedulingJobConfig
```

and a factory method binding it to `<path>`. Annotation attributes become `default` methods on that
interface (so config overrides them); an attribute left unset becomes an **abstract** method, which
makes the corresponding config key mandatory.

`@KoraAppTest` needs the job type in `components` to pull the job into the test graph:

```java
@KoraAppTest(value = Application.class, components = FixedRateJob.class)
```

---

## Method requirements

| Rule | Enforced how |
|---|---|
| Member method of a class (not top-level, not local) | KSP: `Invalid scheduled function: … can be applied only to member functions` |
| No arguments | Generated call site passes none; a parameter breaks compilation |
| Not `suspend` | KSP: `Suspend methods are not supported by the scheduling generator` |
| Not reactive (`Mono`/`Flux`/`CompletionStage`) | Those types are not Kora 2.0 contracts at all |
| Visible from its own package | `private` breaks compilation of the generated module |
| Bearing class present in the graph | Missing `@Component` → the job simply never exists |

`final`/non-`open` is **not** on this list. The migrated examples use `@Component public final class`
(Java) and `@Component class` (Kotlin).

For real parallelism inside a job, the KSP error message points at `StructuredTaskScope` (a JDK preview
API — `--enable-preview` on javac, the Kotlin compiler, tests and the launcher):

```kotlin
fun refreshCaches() =
    StructuredTaskScope.open(
        StructuredTaskScope.Joiner.awaitAllSuccessfulOrThrow<Any>(),
    ).use { scope ->
        scope.fork(Callable { userCache.refresh() })
        scope.fork(Callable { productCache.refresh() })
        scope.join()
    }
```

---

## Cron syntax

Five, six or seven space-separated fields:

```
5 fields: minute hour day-of-month month day-of-week
6 fields: second minute hour day-of-month month day-of-week
7 fields: second minute hour day-of-month month day-of-week year

┌───────────── second (0-59)
│ ┌───────────── minute (0-59)
│ │ ┌───────────── hour (0-23)
│ │ │ ┌───────────── day of the month (1-31)
│ │ │ │ ┌───────────── month (1-12 or JAN-DEC)
│ │ │ │ │ ┌───────────── day of the week (1-7 or SUN-SAT)
│ │ │ │ │ │ ┌───────────── year (empty, 1970-2099, ?)
* * * * * * *
```

Five-field expressions are evaluated with `0` seconds. A missing year field means 1970–2099.
Day-of-week also accepts `0` for Sunday.

| Character | Meaning |
|---|---|
| `*` | every value in the field |
| `?` | no specific value — day-of-month, day-of-week and year only |
| `,` | explicit list, e.g. `6,18` |
| `-` | inclusive range, e.g. `MON-FRI`, `9-17` |
| `/` | step, e.g. `*/10` or `5/10` |

**Not supported:** the Quartz modifiers `L`, `W`, `#`, `C`. Those still require
[kora-aop-scheduling-quartz](../../kora-aop-scheduling-quartz/SKILL.md).

| Expression | Fires |
|---|---|
| `0 * * * * *` | top of every minute |
| `*/10 * * * * *` | every ten seconds |
| `0 0 * * * ?` | top of every hour |
| `0 0 6,19 * * ?` | 06:00 and 19:00 daily |
| `0 0/30 8-10 * * ?` | every 30 min from 08:00 through 10:30 |
| `0 0 9-17 ? * MON-FRI` | hourly 09:00–17:00 on weekdays |
| `0 0 0 25 DEC ?` | Christmas Day, midnight |
| `0 0 0 1 JAN ? 2027` | 1 Jan 2027, midnight |

Cron can also come from config:

```java
@ScheduleWithCron(config = "scheduling.jobs.compaction")
void nightlyCompaction() { }
```

```hocon
# either form works
scheduling.jobs.compaction = "0 0 3 * * ?"

scheduling.jobs.compaction {
  cron = "0 0 3 * * ?"
  telemetry.logging.enabled = true
}
```

With no `value` on the annotation, the `cron` key is mandatory.

---

## JDK vs Quartz

| Need | Module |
|---|---|
| fixed rate / fixed delay / one-shot | **JDK** |
| plain cron, in-process, one instance | **JDK** — `@ScheduleWithCron` |
| cron with `L` / `W` / `#` / `C` | Quartz |
| persistent job store, misfire policy | Quartz |
| clustering — one execution across replicas | Quartz |
| custom Quartz `Trigger` via `@ScheduleWithTrigger` | Quartz |
| `@DisallowConcurrentExecution` / `@PersistJobDataAfterExecution` | Quartz |

The JDK scheduler holds no state anywhere: every replica of your service runs every job.

---

## Error handling

A throwable escaping the method is caught by the job wrapper, which records it on the span
(`StatusCode.ERROR` + `recordException`), tags the duration metric with `error.type`, and logs at WARN:

```
Scheduled Job execution failed with error   jobClass=… jobMethod=… duration=… exceptionType=… exceptionMessage=…
```

The schedule is **not** cancelled — the next run happens as planned, for all four annotations. (This is
the wrapper's doing; a raw `ScheduledExecutorService` would have cancelled a periodic task.)

Kora does not retry. Options:

```java
@ScheduleWithFixedDelay(config = "scheduling.jobs.import")
void importData() {
    try {
        doWork();
    } catch (Exception e) {
        log.error("import failed", e);   // swallow: keep your own log shape
    }
}
```

or delegate the work to a method carrying [`@Retryable`](../../kora-aop-resilient/SKILL.md) — that
aspect *is* proxy-based, so its own `open`/non-`final` rules apply to the class holding it.

---

## Telemetry

Toggles live under `scheduling.telemetry` and can be overridden per job under the job's own `config`
path. Full key list in [scheduling-config-reference.md](scheduling-config-reference.md).

If tracing, metrics and logging are all disabled for a job, Kora installs a no-op telemetry and the job
costs nothing.

### Metrics (Micrometer)

- **Meter:** `scheduling.job.duration` — a **`Timer`**. With the default Prometheus registry naming that
  is exposed as `scheduling_job_duration_seconds_*`; other registries name it their own way.
- **Enabled:** `scheduling.telemetry.metrics.enabled`, default **`false`**. A `MeterRegistry` component
  must also exist.
- **Tags:** `code.function.name`, `system.name.simple` (`Class#method`),
  `system.name.canonical` (`fqcn#method`), `error.type` (empty string on success), anything in
  `metrics.tags`, and conditionally `system.config` — see [below](#the-systemconfig-tag).
- **SLO:** `metrics.slo`, a list of durations; the default is the framework-wide 14-bucket ladder
  (1 ms → 90 s).

### Tracing (OpenTelemetry)

- **Enabled:** `scheduling.telemetry.tracing.enabled`, default **`true`** — but it is a no-op unless a
  `Tracer` component is in the graph.
- **Span:** name `scheduling <fqcn>#<method>`, kind `INTERNAL`, started from a **root** context (a
  scheduled run never continues an inbound trace).
- **Attributes:** `code.function.name`, `system.name.simple`, `system.name.canonical`, anything in
  `tracing.attributes`, and conditionally `system.config` — see [below](#the-systemconfig-tag).

### Logging

- **Enabled:** `scheduling.telemetry.logging.enabled`, default **`false`**.
- Start: DEBUG `Scheduled Job execution started`. End: INFO `Scheduled Job execution completed`, or
  WARN `Scheduled Job execution failed with error`.
- Structured key-values: `jobClass`, `jobMethod`, `duration` (ms), optional `jobConfigPath`, and
  `exceptionType` / `exceptionMessage` on failure.
- **Logger name is `<fqcn>#<method>`**, not the class name. Logback splits logger names on `.`, so
  `com.example` covers it but `com.example.Jobs` does **not**. Set the level on a package prefix.

### The `system.config` tag

This one is not uniform, and it matters if you group dashboards by it. The value is whatever the
generated code passes as the telemetry's *job config path*:

| Declaration | Java | Kotlin |
|---|---|---|
| any annotation with `config = "<path>"` | `<path>` | `<path>` |
| `@ScheduleAtFixedRate` / `@ScheduleWithFixedDelay` / `@ScheduleOnce` without `config` | `<fqcn>#<method>` | *absent* |
| `@ScheduleWithCron` without `config` | *absent* | *absent* |

So in a Java service a config-less timer job still carries `system.config`, holding the same string as
`system.name.canonical` rather than a real config path; in Kotlin the tag is simply not there. Group by
`system.name.canonical` if you want one query that works for both languages, and give jobs a `config`
path when you want `system.config` to mean something.

---

## See Also

- [scheduling-config-reference.md](scheduling-config-reference.md) — complete configuration reference
- [graceful-shutdown-reference.md](graceful-shutdown-reference.md) — the real 2.0 shutdown path
- [kora-aop-scheduling-quartz](../../kora-aop-scheduling-quartz/SKILL.md) — persistence, clustering, custom triggers
- [kora-telemetry-metrics](../../kora-telemetry-metrics/SKILL.md) — wiring the `MeterRegistry`
