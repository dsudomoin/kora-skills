---
name: kora-aop-scheduling-jdk
description: "In-process scheduled jobs in Kora 2.x — @ScheduleAtFixedRate, @ScheduleWithFixedDelay, @ScheduleOnce and the new @ScheduleWithCron from io.koraframework.scheduling.jdk.annotation, contributed by SchedulingJdkModule (artifact io.koraframework:scheduling-jdk): one platform timer thread dispatches each run onto a fresh virtual thread. Covers the config attribute for externalising timings, the scheduling.jdk.shutdownWait / maxConcurrentExecutions and scheduling.telemetry keys, cron DST handling, per-job telemetry overrides, and why a scheduled method must be a no-argument, non-suspend member of a @Component. Use for heartbeat, cleanup, cache-warm and in-process cron jobs. For one execution per cluster use kora-aop-scheduling-db; for Quartz triggers, misfire policies or L/W/# cron use kora-aop-scheduling-quartz."
license: Apache-2.0
metadata:
  kora-version: "2.x"
---

# Kora AOP Scheduling (JDK)

> **Kora sub-skill — obey the [kora-v2 meta rules](../../SKILL.md) on every task:** **R0** ground the workspace on Kora 2.0 refs before starting (framework source at tag `2.0.0.RC2` + `kora-examples` at `migration/2.0` + Kora 2.0 docs at koraframework.io/v2, which trail the source; 1.x `kora-docs` pages are never an authority) · **R1** read this sub-skill before writing code · **R2** Kora 2.0 APIs only — no Spring/Micronaut/Quarkus, no Kora 1.x APIs, no invented annotations or config keys · **R3** journal any incorrect Kora usage. Add comments/Javadoc only if asked.

| | |
|---|---|
| **Artifact** | `io.koraframework:scheduling-jdk` (version from `io.koraframework:kora-bom`) |
| **Module** | `io.koraframework.scheduling.jdk.SchedulingJdkModule` (extends `io.koraframework.scheduling.common.SchedulingModule`) |
| **Annotations** | `io.koraframework.scheduling.jdk.annotation.*` |
| **Processor** | Java `annotationProcessor "io.koraframework:annotation-processors"` · Kotlin `ksp("io.koraframework:symbol-processors")` |
| **Config roots** | `scheduling.jdk` · `scheduling.telemetry` · one arbitrary path per job via `config = "…"` |
| **Runtime** | `VirtualThreadSchedulingJdkExecutor` — one platform timer thread, one fresh virtual thread per run |

In-process scheduling: a single platform timer thread decides *when*, and every run executes on its
own virtual thread. No external scheduler, no job store — jobs live for the lifetime of the process
only, and every replica runs every job. The module has its **own cron evaluator**, so a plain cron job
does not require Quartz.

---

## What changed from Kora 1.x

Read this before porting a 1.x service: none of it is covered by the migration guides, which mention
only the Quartz `@ScheduleWithTrigger` change.

| 1.x | 2.0 | Consequence if you skip it |
|---|---|---|
| `ru.tinkoff.kora.scheduling.jdk.annotation.*` | `io.koraframework.scheduling.jdk.annotation.*` | Compile error — loud, harmless |
| `ru.tinkoff.kora:scheduling-jdk`, BOM `kora-parent` | `io.koraframework:scheduling-jdk`, BOM `io.koraframework:kora-bom` | Unresolved dependency |
| Kotlin processor `scheduling-ksp` | `scheduling-symbol-processor`, normally via the aggregate `symbol-processors` | `scheduling-ksp` on Central is a 1.x leftover, **not** in the 2.0 BOM |
| `scheduling.shutdownWait` | **`scheduling.jdk.shutdownWait`** | Stale key is an unknown HOCON key: ignored silently, shutdown falls back to 30 s |
| `scheduling.threads` (pool size) | **removed** — runs are virtual threads; the optional cap is `scheduling.jdk.maxConcurrentExecutions` (default unlimited) | Ignored silently (see [Thread model](#thread-model)) |
| Cron only via `scheduling-quartz` | **`@ScheduleWithCron` in `scheduling-jdk`** | You may be pulling in Quartz for nothing |
| "fixed rate may overlap" | **never overlaps** | See [Overlap](#overlap-fixed-rate-does-not-overlap) |
| "class must be non-`final` / `open`" | **not required by scheduling** | See [What a scheduled method must satisfy](#what-a-scheduled-method-must-satisfy) |
| `telemetry.metrics.enabled` default `true` | **default `false`** | Job metrics silently absent |

---

## Quick Start

### 1. Dependency

Versions come from the BOM — never pin an individual `io.koraframework:*` artifact.

```groovy
// build.gradle (Java)
configurations {
    koraBom
    annotationProcessor.extendsFrom(koraBom); compileOnly.extendsFrom(koraBom); implementation.extendsFrom(koraBom)
    api.extendsFrom(koraBom); testImplementation.extendsFrom(koraBom); testAnnotationProcessor.extendsFrom(koraBom)
}

dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")   // koraVersion=2.0.0.RC2
    annotationProcessor "io.koraframework:annotation-processors" // mandatory — generates the job module

    implementation "io.koraframework:scheduling-jdk"
    implementation "io.koraframework:config-hocon"
    implementation "io.koraframework:logging-logback"
}
```

```kotlin
// build.gradle.kts (Kotlin)
dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))
    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")

    implementation("io.koraframework:scheduling-jdk")
    implementation("io.koraframework:config-hocon")
    implementation("io.koraframework:logging-logback")
}
```

### 2. Plug the module into `@KoraApp`

```java
@KoraApp
public interface Application extends
    HoconConfigModule,
    LogbackModule,
    SchedulingJdkModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

### 3. Declare a scheduled component

```java
package com.example.app.jobs;

import io.koraframework.common.annotation.Component;
import io.koraframework.scheduling.jdk.annotation.ScheduleAtFixedRate;

import java.time.temporal.ChronoUnit;

@Component
public final class ScheduledJobs {   // final is fine — scheduling does not proxy the class

    @ScheduleAtFixedRate(initialDelay = 30, period = 60, unit = ChronoUnit.SECONDS)
    void heartbeat() {
        // runs every 60s
    }
}
```

The processor emits a `@Module` interface `$ScheduledJobs_SchedulingModule` next to your class; the
`@KoraApp` graph picks it up on its own. You never reference it by hand.

---

## Annotations

All four live in `io.koraframework.scheduling.jdk.annotation`, target `METHOD`, and carry a
`String config() default ""`.

| Annotation | Attributes | Interval measured | Overlap |
|---|---|---|---|
| `@ScheduleAtFixedRate` | `initialDelay` (long, `0`), `period` (long, `0`), `unit` (`ChronoUnit`, `MILLIS`), `config` | start → start | never |
| `@ScheduleWithFixedDelay` | `initialDelay` (long, `0`), `delay` (long, `0`), `unit` (`ChronoUnit`, `MILLIS`), `config` | end → start | never |
| `@ScheduleOnce` | `delay` (long, `0`), `unit` (`ChronoUnit`, `MILLIS`), `config` | one run after `delay` | n/a |
| `@ScheduleWithCron` | `value` (String, `""`), `config` | next cron fire time | never |

`unit` applies to the annotation's own numbers only; it is not a config key. Durations that come from
config are written as HOCON/YAML durations (`"30s"`, `5ms`, `2m`).

Either the primary attribute or `config` must be set. `period`/`delay` of `0` and a blank cron `value`
count as *unset*, and the build fails with, e.g.:

```
Either period() or config() annotation parameter must be provided
```

### Overlap: fixed rate does **not** overlap

`VirtualThreadSchedulingJdkExecutor` schedules the next periodic run only after the current one has
returned, and each job additionally serialises its runs behind a per-job lock. A fixed-rate job keeps
its original timetable: if a run overruns the period, the next run starts as soon as it returns
(catching up), never in parallel with it. The framework test
`fixedRateCatchesUpWithoutOverlappingExecutions` pins exactly that.

So the choice between the two periodic annotations is about **where the interval is measured**, not
about overlap:

- `@ScheduleAtFixedRate` — tries to keep a fixed cadence; after an overrun it starts the next run late.
- `@ScheduleWithFixedDelay` — always leaves `delay` idle after the previous run returned, so the cadence
  drifts with execution time.

Two *different* jobs can run at the same time — each run gets its own virtual thread, subject only to
the optional `maxConcurrentExecutions` cap below.

### `@ScheduleWithCron`

```java
@ScheduleWithCron("0 0 3 * * ?")     // 03:00 every day, JVM default time zone
void nightlyCompaction() { }
```

5, 6 or 7 space-separated fields (`[second] minute hour day-of-month month day-of-week [year]`),
`* , - / ?`, month aliases `JAN`–`DEC` and day aliases `SUN`–`SAT` (including ranges, lists and steps
such as `JUL-OCT`, `JUL/2`, `WED-FRI`). Quartz modifiers `L`, `W`, `#`, `C` are **not** supported and
`@ScheduleWithCron` is still in-process only. The expression is parsed while the graph is being built,
so a bad expression fails startup with `IllegalArgumentException`, not at compile time.

**Daylight-saving transitions** (JVM default zone): a local time that does not exist on the
spring-forward day is **skipped**, not shifted — `0 30 2 * * ?` in `Europe/Berlin` does not fire on the
day 02:30 is missing. A local time that occurs twice on the fall-back day fires **twice**, once per
occurrence, in instant order. Schedule daily jobs outside the transition hour, or run the JVM in UTC,
if that matters.

Details, config form and the routing rule against Quartz:
[references/jdk-scheduling-reference.md](references/jdk-scheduling-reference.md).

---

## What a scheduled method must satisfy

The scheduling processor does **not** generate an AOP proxy. It generates a module whose factory
method takes `ValueOf<YourClass>` and calls `target.get().yourMethod()`. Therefore:

**Required**

- the bearing class is a graph component (`@Component`, or produced by a `@Module` factory method);
- the method is a **member** method (not top-level, not local) with **no arguments**;
- the method is visible from its own package — `private` will not compile;
- the method is **not** `suspend` (Kotlin) and not reactive. Kora 2.0 contracts are synchronous.

**Not required**

- the class does **not** have to be non-`final` (Java) or `open` (Kotlin). The migrated examples ship
  `@Component public final class FixRateScheduler` and `@Component class FixRateScheduler` and both
  schedule correctly.

`open`/non-`final` becomes necessary only if you stack a *proxy-based* aspect on the same class —
`@Log`, `@Retryable`, `@CircuitBreakable`, `@Timeout`, `@Cacheable`. That requirement belongs to those
aspects, not to scheduling; see [kora-aop-logging](../kora-aop-logging/SKILL.md) and
[kora-aop-resilient](../kora-aop-resilient/SKILL.md).

A `suspend` scheduled function is rejected at build time, not silently ignored:

```
Suspend methods are not supported by the scheduling generator.
…
For structured concurrency, enable Java preview features with --enable-preview and use StructuredTaskScope
…
Fix: remove suspend from the function.
```

---

## Externalized parameters (`config`)

With `config = "<path>"` the processor generates a `SchedulingJobConfig` subtype bound to that path.
Annotation attributes become **defaults** of the generated config; values in the config file win.
Omitting the annotation attribute entirely makes the config key **mandatory**.

```java
@ScheduleAtFixedRate(config = "scheduling.jobs.heartbeat")
void heartbeat() { }
```

```hocon
scheduling.jobs.heartbeat {
  initialDelay = 10s
  period = 30s
}
```

| Annotation | Keys under the config path |
|---|---|
| `@ScheduleAtFixedRate` | `initialDelay`, `period` |
| `@ScheduleWithFixedDelay` | `initialDelay`, `delay` |
| `@ScheduleOnce` | `delay` |
| `@ScheduleWithCron` | `cron` — or set the path itself to the expression string |

Every config path additionally accepts a `telemetry { … }` block that overrides the global scheduling
telemetry for that one job.

Pick a path that does not collide with the module's own sections. `scheduling.jobs.<name>` (as in the
official examples) is safe; `scheduling.jdk` and `scheduling.telemetry` are taken.

---

## Module configuration

Defaults below are the ones in source — nothing here is aspirational.

```hocon
scheduling {
  jdk {
    shutdownWait = 30s              # graceful drain budget for running jobs (default 30s)
    maxConcurrentExecutions = 100   # cap on runs executing at once (default Integer.MAX_VALUE = unlimited)
  }

  telemetry {
    logging.enabled = false      # default false
    metrics.enabled = false      # default false
    tracing.enabled = true       # default true (no-op without a Tracer component)
  }
}
```

There is **no `scheduling.threads` key in Kora 2.0.** Writing one is not an error — it is an unknown
HOCON key, silently ignored. Runs are virtual threads, so there is no pool to size; the only knob is the
concurrency cap `scheduling.jdk.maxConcurrentExecutions`.

Full key list, per-job overrides, metric names and span attributes:
[references/scheduling-config-reference.md](references/scheduling-config-reference.md).

---

## Thread model

`SchedulingJdkModule` provides `VirtualThreadSchedulingJdkExecutor` as the default
`SchedulingJdkExecutor`:

- **one platform timer thread**, `kora-jdk-scheduler-timer` (non-daemon), only decides *when* a run is
  due;
- every run executes on a **fresh virtual thread** named `kora-jdk-scheduler-job-N` — threads are not
  reused and do not inherit `InheritableThreadLocal` values;
- `scheduling.jdk.maxConcurrentExecutions` caps how many runs execute at once across **all** jobs.
  Default `Integer.MAX_VALUE` (unlimited); a value below 1 is treated as 1. Runs over the cap wait in a
  FIFO queue.

The executor does not depend on the jobs, and the job count does not size anything: one job or fifty,
annotation-only or `config`-driven, a slow job no longer delays the others. Blocking calls inside a job
(JDBC, HTTP clients) are ordinary blocking calls on a virtual thread.

Set `maxConcurrentExecutions` only when the jobs share a scarce resource — for example to keep
concurrent jobs below the JDBC pool size.

---

## Graceful shutdown

1. Graph release runs in reverse dependency order, so every job is released before the executor it
   depends on. A job's `release()` cancels its future schedule with `cancel(false)` and returns at
   once — it does **not** wait for an in-flight run, and it does not interrupt it.
2. The executor's `release()` stops accepting work and cancels every periodic task, then waits up to
   `scheduling.jdk.shutdownWait` (default 30 s) for running jobs to finish.
3. If the budget runs out it calls `shutdownNow()`: queued runs are dropped and **running job threads
   are interrupted**, and it logs `SchedulingJdkExecutor failed completing graceful shutdown in PT30S`.
   `release()` then returns without waiting further.

So `shutdownWait` **does** bound shutdown, and an interrupt is the signal that the budget is gone. A
job should still be short or cooperatively bounded (batch cap, in-run deadline, timeouts on blocking
calls) so it finishes inside the budget, and it should handle `InterruptedException` /
`Thread.currentThread().isInterrupted()` by stopping cleanly. Because the job itself no longer blocks
release, components the job uses may be released while its last run is still draining — another reason
to keep runs short.

Patterns and the failure modes: [references/graceful-shutdown-reference.md](references/graceful-shutdown-reference.md).

---

## Error handling

A throwable escaping the job is caught by the job wrapper, recorded on the span, counted under the
`error.type` metric tag and — with `scheduling.telemetry.logging.enabled = true` — logged at WARN as
`Scheduled Job execution failed with error`. The schedule
survives — the next run happens as planned, for every annotation including `@ScheduleWithCron`.

Kora does not retry the job for you. For retries put [`@Retryable`](../kora-aop-resilient/SKILL.md) on
an inner method (that one is proxy-based, so its `open`/non-`final` rules apply), or handle the failure
in the job body.

---

## Common pitfalls

| Symptom | Cause / fix |
|---|---|
| Job never fires | The class is not in the graph. Add `@Component` (or a `@Module` factory). `final`/non-`open` is **not** the cause |
| `Either period() or config() annotation parameter must be provided` | Annotation has neither a non-zero primary attribute nor `config` |
| `Suspend methods are not supported by the scheduling generator` | Drop `suspend`; use `StructuredTaskScope` inside a plain function for parallelism |
| Timings from config ignored | The `config` path in the annotation and the path in the file disagree; or the key name is wrong for that annotation (`period` vs `delay`) |
| No job metrics | `scheduling.telemetry.metrics.enabled` defaults to **`false`** — and a `MeterRegistry` component must exist |
| Jobs wait for each other | `scheduling.jdk.maxConcurrentExecutions` is set too low; the default is unlimited |
| `SchedulingJdkExecutor failed completing graceful shutdown` | A run outlived `scheduling.jdk.shutdownWait` and was interrupted — bound the run or raise the budget |
| Daily cron job ran twice / not at all | DST fall-back repeats the local time, spring-forward skips it; move the job out of the transition hour or use UTC |
| `scheduling.threads` / `scheduling.shutdownWait` have no effect | Both are 1.x keys. Use `scheduling.jdk.shutdownWait`; the only concurrency knob is `scheduling.jdk.maxConcurrentExecutions` |

---

## Testing

The migrated examples drive the job component into the test graph explicitly and then poll:

```java
@KoraAppTest(value = Application.class, components = FixedRateJob.class)
class HeartbeatTests {

    @TestComponent
    private ScheduledJobs jobs;

    @Test
    void scheduled() {
        Awaitility.await().atMost(Duration.ofSeconds(3)).until(() -> jobs.getTicks() > 3);
    }
}
```

`components` takes the job wrapper type — `FixedRateJob`, `FixedDelayJob`, `RunOnceJob` or `CronJob`
from `io.koraframework.scheduling.jdk` — matching the annotation under test. See
[kora-testing-junit-java](../kora-testing-junit-java/SKILL.md) /
[kora-testing-junit-kotlin](../kora-testing-junit-kotlin/SKILL.md).

---

## References, assets, scripts

| File | Purpose |
|---|---|
| [references/jdk-scheduling-reference.md](references/jdk-scheduling-reference.md) | Per-annotation reference, generated code, cron syntax, telemetry |
| [references/scheduling-config-reference.md](references/scheduling-config-reference.md) | Complete `scheduling.*` key set, HOCON + YAML, per-job overrides |
| [references/graceful-shutdown-reference.md](references/graceful-shutdown-reference.md) | The shutdown path, what `shutdownWait` bounds, cooperative-cancellation patterns |
| [assets/ScheduledJobs.java.template](assets/ScheduledJobs.java.template) | Java scheduled-jobs starter |
| [assets/ScheduledJobs.kt.template](assets/ScheduledJobs.kt.template) | Kotlin scheduled-jobs starter |
| [scripts/setup-jdk.sh](scripts/setup-jdk.sh) | Add `scheduling-jdk`, a job template and config to a project (`--dry-run` supported) |

---

## Which scheduler?

Kora ships three schedulers. Same table in all three scheduling sub-skills:

| Need | JDK (this skill) | Quartz | DB (db-scheduler) |
|---|---|---|---|
| Extra infrastructure | none | none; a JDBC JobStore + Quartz tables for persistence/clustering | a JDBC `DataSource` + one table |
| Who runs a firing | every replica | every replica (`RAMJobStore`); one node when clustered on a JDBC JobStore | one replica, cluster-wide |
| Survives restart | no | only with a JDBC JobStore | yes — the next execution time lives in the table |
| Fixed rate | `@ScheduleAtFixedRate` | via a custom `Trigger` | no |
| Fixed delay | `@ScheduleWithFixedDelay` | via a custom `Trigger` | `@ScheduleWithFixedDelay` |
| One-shot | `@ScheduleOnce`, every process start | via a custom `Trigger` | `@ScheduleOnce`, one pending execution cluster-wide |
| Cron | Kora `CronExpression`, 5/6/7 fields, no `L W # C` | Quartz cron, 6/7 fields, `L W # C` | db-scheduler `CronSchedule`, Spring-style 6 fields |
| Custom trigger, misfire policy, `JobDataMap` | no | yes | no |
| Runs on | virtual threads, cap `maxConcurrentExecutions` (unlimited) | Quartz `SimpleThreadPool` platform threads (10) | virtual threads, cap `executionParallelism` (10) |
| Shutdown | waits `shutdownWait`, then interrupts | `waitForJobComplete`, never interrupts | waits `shutdownWait`, then interrupts |

---

## Related skills

- [kora-aop-scheduling-db](../kora-aop-scheduling-db/SKILL.md) — use it when a job must run **once per
  cluster** and survive restarts: db-scheduler on a table in your own database
- [kora-aop-scheduling-quartz](../kora-aop-scheduling-quartz/SKILL.md) — use it for
  **`@ScheduleWithTrigger` with a custom Quartz `Trigger`, misfire policies,
  `@DisallowConcurrentExecution` / `@PersistJobDataAfterExecution`, a Quartz JDBC JobStore, or the
  Quartz-only cron modifiers `L` `W` `#` `C`**. Plain in-process cron does not need it.
- [kora-di-compile](../kora-di-compile/SKILL.md) — `@Component`, `@Module`, `@Root`, graph errors
- [kora-config-hocon](../kora-config-hocon/SKILL.md) / [kora-config-yaml](../kora-config-yaml/SKILL.md) — config sources for the `config` attribute
- [kora-aop-logging](../kora-aop-logging/SKILL.md) — `@Log` / `@Mdc` on job methods
- [kora-aop-resilient](../kora-aop-resilient/SKILL.md) — `@Retryable` / `@Timeout` around job work
- [kora-telemetry-metrics](../kora-telemetry-metrics/SKILL.md) — wiring the `MeterRegistry` the job metric needs
