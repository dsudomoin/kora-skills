---
name: kora-aop-scheduling-quartz
description: "Quartz-backed scheduling in Kora 2.x — io.koraframework.scheduling.quartz.QuartzModule from artifact scheduling-quartz (Quartz 2.5.2). Covers @ScheduleWithCron (cron expression, identity, config path), @ScheduleWithTrigger(MyJob.class) taking a class tag that binds an org.quartz.Trigger from the graph, @DisallowConcurrentExecution, @PersistJobDataAfterExecution, the org.quartz.JobExecutionContext job argument, scheduling.quartz.properties / scheduling.quartz.waitForJobComplete / scheduling.telemetry config, and JDBC JobStore clustering. Use for cron jobs, custom Quartz triggers, misfire policies or persistent job stores; for fixed-rate, fixed-delay and one-shot timers use kora-aop-scheduling-jdk."
license: Apache-2.0
metadata:
  kora-version: "2.x"
---

# Kora Quartz Scheduling

> **Kora sub-skill — obey the [kora-v2 meta rules](../../SKILL.md) on every task:** **R0** ground the workspace on Kora 2.0 refs before starting (framework source at tag `2.0.0.RC1` + `kora-examples` at `migration/2.0`; `kora-docs` is 1.x only) · **R1** read this sub-skill before writing code · **R2** Kora 2.0 APIs only — no Spring/Micronaut/Quarkus, no Kora 1.x APIs, no invented annotations or config keys · **R3** journal any incorrect Kora usage. Add comments/Javadoc only if asked.

| | |
|---|---|
| **Artifact** | `io.koraframework:scheduling-quartz` (BOM `io.koraframework:kora-bom:2.0.0.RC1`) |
| **Module** | `io.koraframework.scheduling.quartz.QuartzModule` |
| **Annotations** | `io.koraframework.scheduling.quartz.*` — a flat package, **no** `.annotation` segment |
| **Quartz** | `org.quartz-scheduler:quartz:2.5.2`, pulled in transitively (`api`) |
| **Processor** | `annotation-processors` (Java) / `symbol-processors` (KSP) |

Annotate a method on a graph component with `@ScheduleWithCron` or `@ScheduleWithTrigger`.
The processor generates a `$<Class>_<method>_Job` wrapper plus a `$<Class>_SchedulingModule`
`@Module`; `KoraQuartzJobRegistrar` registers every generated job with the Quartz `Scheduler`
at graph init.

**This is not an AOP proxy.** The generated job holds a reference to your component and calls
the method directly, so the class may stay `final` (Java) / non-`open` (Kotlin) — the canonical
examples do exactly that. `open` is only needed if you *also* stack a method-wrapping aspect
(`@Log`, `@Retryable`, `@Timeout`) on the same class.

---

## Quick start

### 1. Dependencies

```groovy
// build.gradle (Java)
dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")   // koraVersion=2.0.0.RC1
    annotationProcessor "io.koraframework:annotation-processors"

    implementation "io.koraframework:scheduling-quartz"
    implementation "io.koraframework:config-hocon"
    implementation "io.koraframework:logging-logback"
}
```

```kotlin
// build.gradle.kts (Kotlin)
dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))
    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")

    implementation("io.koraframework:scheduling-quartz")
    implementation("io.koraframework:config-hocon")
    implementation("io.koraframework:logging-logback")
}
```

### 2. Plug the module into `@KoraApp`

```java
import io.koraframework.application.graph.KoraApplication;
import io.koraframework.common.annotation.KoraApp;
import io.koraframework.config.hocon.HoconConfigModule;
import io.koraframework.logging.logback.LogbackModule;
import io.koraframework.scheduling.quartz.QuartzModule;

@KoraApp
public interface Application extends HoconConfigModule, LogbackModule, QuartzModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

### 3. A cron job

```java
import io.koraframework.common.annotation.Component;
import io.koraframework.scheduling.quartz.ScheduleWithCron;

@Component
public final class CronScheduler {

    @ScheduleWithCron("0 0 3 * * ?")   // daily at 03:00
    void nightlyReport() {
        // ...
    }
}
```

The method must be reachable from the generated code, which lands in the **same package** —
package-private (Java) / `internal` or public (Kotlin) is enough.

---

## What's in this skill

| File | Purpose |
|------|---------|
| [references/quartz-scheduling-reference.md](references/quartz-scheduling-reference.md) | Every annotation and its real attributes, generated code, `JobExecutionContext`, cron grammar, error propagation |
| [references/scheduling-config-reference.md](references/scheduling-config-reference.md) | `scheduling.*` keys, Quartz property passthrough, telemetry, JDBC JobStore and clustering |
| [references/graceful-shutdown-reference.md](references/graceful-shutdown-reference.md) | What shutdown actually does (there is **no** thread interrupt) and how to bound a long job |
| [assets/ScheduledJobs.java.template](assets/ScheduledJobs.java.template) | Java starter |
| [assets/ScheduledJobs.kt.template](assets/ScheduledJobs.kt.template) | Kotlin starter |
| [scripts/setup-quartz.sh](scripts/setup-quartz.sh) | Scaffold deps, jobs class and config (`--dry-run` supported) |
| [scripts/create-cron-job.sh](scripts/create-cron-job.sh) | Generate one cron job class + config entry (`--dry-run` supported) |
| [scripts/validate-cron.sh](scripts/validate-cron.sh) | Sanity-check a Quartz cron expression (read-only) |

---

## Quartz or JDK?

**Both modules ship a `@ScheduleWithCron`** in Kora 2.0 — they are different annotations in
different packages and route to different schedulers:

| Annotation | Package | Scheduler |
|---|---|---|
| `@ScheduleWithCron` | `io.koraframework.scheduling.quartz` | Quartz |
| `@ScheduleWithCron` | `io.koraframework.scheduling.jdk.annotation` | JDK `ScheduledThreadPoolExecutor` |

Importing the wrong one compiles and silently schedules the job on the other engine. Check the
import, not the simple name.

| Need | Use |
|------|-----|
| Custom `org.quartz.Trigger` (`@ScheduleWithTrigger`) | **Quartz** |
| Misfire policies, Quartz calendars, `JobExecutionContext` / `JobDataMap` | **Quartz** |
| Persistent or clustered job store (JDBC) | **Quartz** |
| Cron with no extra dependency | **JDK** — `io.koraframework.scheduling.jdk.annotation.ScheduleWithCron` (also accepts 5-field cron) |
| Fixed rate / fixed delay / run once | **JDK** — see [kora-aop-scheduling-jdk](../kora-aop-scheduling-jdk/SKILL.md) |

---

## Annotations

| Annotation | Target | Attributes |
|------------|--------|------------|
| `@ScheduleWithCron` | method | `value` (cron), `identity` (trigger identity), `config` (config path) — all `String`, all default `""` |
| `@ScheduleWithTrigger` | method | `Class<?> value()` — a **class tag**, not a nested `@Tag`, not a string |
| `@DisallowConcurrentExecution` | method | none |
| `@PersistJobDataAfterExecution` | method | none |

`@DisallowConcurrentExecution` and `@PersistJobDataAfterExecution` are **Kora's own**
annotations in `io.koraframework.scheduling.quartz` — not re-exports. The generators also
accept the Quartz originals (`org.quartz.DisallowConcurrentExecution`,
`org.quartz.PersistJobDataAfterExecution`), but only **on the class**; the Kora ones are read
**on the method only**. Either form ends up as the `org.quartz.*` annotation on the generated
job class.

### `@ScheduleWithTrigger` — the 2.0 signature

```java
@KoraApp
public interface Application extends QuartzModule {

    @Tag(TriggerScheduler.class)
    default Trigger myTrigger() {
        return TriggerBuilder.newTrigger()
            .withIdentity("myTrigger")
            .startNow()
            .withSchedule(SimpleScheduleBuilder.simpleSchedule()
                .withIntervalInSeconds(5)
                .repeatForever())
            .build();
    }
}

@Component
public final class TriggerScheduler {

    @ScheduleWithTrigger(TriggerScheduler.class)   // class tag passed directly
    void schedule() { }
}
```

```kotlin
@ScheduleWithTrigger(TriggerScheduler::class)
fun schedule() { }
```

The class you pass becomes a `@Tag` on the `org.quartz.Trigger` parameter of the generated
factory method, so the graph must contain a `Trigger` under exactly that tag. The convention is
to tag with the job class itself.

**Kora 1.x wrote `@ScheduleWithTrigger(@Tag(MyJob.class))`.** In 2.0 the attribute is
`Class<?>`, so the nested form fails at compile time:
`error: annotation not valid for an element of type Class<?>`.

### `@ScheduleWithCron`

```java
@ScheduleWithCron("0 0 9 ? * MON-FRI")                 // 09:00 on weekdays
void morningReport() { }

@ScheduleWithCron(value = "0 0 * * * ?", identity = "hourly")
void hourly() { }

@ScheduleWithCron(config = "jobs.nightly")             // cron comes from config
void nightly() { }
```

Quartz cron is the 6- or 7-field form (`sec min hour day-of-month month day-of-week [year]`);
day-of-month and day-of-week are mutually exclusive, so one of them must be `?`.

With `config`, the node may be a bare string (`jobs.nightly = "0 0 3 * * ?"`) or an object with
a `cron` field. If `value` is also set it is the fallback used when the config node is absent;
if `value` is empty the config node is **required** and a missing one fails the graph build.
Only the `config` form supports per-job telemetry overrides. See
[scheduling-config-reference.md](references/scheduling-config-reference.md#per-job-configuration).

---

## Contracts

- **Synchronous only.** A scheduled method returns `void` / `Unit` and takes either no
  arguments or a single `org.quartz.JobExecutionContext`.
- Kotlin `suspend` is rejected by KSP: *"Suspend methods are not supported by the scheduling
  generator"* — the message points at `StructuredTaskScope` for real parallelism.
- `Mono`/`Flux`/`CompletionStage` are not Kora 2.0 contracts anywhere, scheduling included.
  `Context` no longer exists in the framework; use the `JobExecutionContext` argument for
  Quartz fire-time data.
- The enclosing class must be a graph component (`@Component`, or supplied by a `@Module`
  factory method) — the generated module takes it as a constructor dependency.

---

## Thread model

Quartz jobs do **not** run on Kora's virtual threads. `KoraQuartzScheduler` builds a plain
`StdSchedulerFactory`, so jobs run on Quartz's own `SimpleThreadPool`: platform threads named
`kora-quartz-scheduler_Worker-N`, `org.quartz.threadPool.threadCount` = 10 by default,
non-daemon. That thread count is the hard ceiling on concurrent job executions across the whole
application; oversubscribing it delays firings and produces misfires.

Kora still binds its own context around the call — `KoraQuartzJob.execute` installs the Kora
`MDC`, a fresh OpenTelemetry context and the scheduling `Observation` before invoking your
method, so logging and tracing work normally inside the job body.

---

## Configuration essentials

```hocon
scheduling {
  quartz {
    # raw org.quartz.* properties, passed to StdSchedulerFactory
    properties {
      "org.quartz.threadPool.threadCount" = "10"
    }
    waitForJobComplete = true   # default true — shutdown blocks until running jobs finish
  }

  telemetry {
    logging.enabled = true      # default false
    metrics.enabled = true      # default false
    tracing.enabled = true      # default true
  }
}
```

Kora forces `org.quartz.scheduler.instanceName = kora-quartz-scheduler` and
`org.quartz.scheduler.instanceId = AUTO` as *defaults* — both are overridable from
`scheduling.quartz.properties`. Everything else comes from the `org/quartz/quartz.properties`
bundled in the Quartz jar (`SimpleThreadPool`, `RAMJobStore`, `misfireThreshold = 60000`).

**Keys that no longer exist.** The 1.x root `quartz { "org.quartz.*" }` block and
`scheduling.waitForJobComplete` are not read by 2.0. Unknown HOCON keys are ignored without a
warning, so a stale config starts green on Quartz defaults instead of your settings. Full table
in [scheduling-config-reference.md](references/scheduling-config-reference.md).

---

## Shutdown — there is no interrupt

`KoraQuartzScheduler.release()` calls `Scheduler.shutdown(waitForJobComplete)`. Quartz's
`SimpleThreadPool.shutdown(...)` only clears a run flag on its workers — **a busy worker is
never interrupted**, and Quartz documents it as *"Jobs currently in progress will complete."*

So `Thread.currentThread().isInterrupted()` inside a Kora Quartz job is dead code: it never
becomes true because of shutdown. `waitForJobComplete = false` does not cancel anything either;
it only stops `release()` from blocking, and the non-daemon worker keeps running. Kora's
generated job class is `final` and `KoraQuartzJob.execute` is `final`, so it cannot implement
`org.quartz.InterruptableJob` and `Scheduler.interrupt(...)` cannot reach it.

Bound the work inside the job body instead (deadline or batch cap) and make jobs idempotent and
restartable. Note the Quartz wait has no timeout of its own — unlike `scheduling-jdk`, which caps
it at `scheduling.jdk.shutdownWait` (30s) and then calls `shutdownNow()`, so `isInterrupted()`
*does* work there. Shutdown is not shared code: `scheduling-common` carries only config and
telemetry. Patterns and the full contrast in
[graceful-shutdown-reference.md](references/graceful-shutdown-reference.md).

---

## Error handling

`KoraQuartzJob.execute` records the failure on the observation and **rethrows**, so the
exception reaches Quartz and the trigger's misfire/refire policy applies. With
`scheduling.telemetry.logging.enabled = true` Kora logs it at WARN with `exceptionType` and
`exceptionMessage`. Catch inside the method if a failure should not surface as a Quartz job
failure.

---

## Common pitfalls

| Symptom | Cause / fix |
|---------|-------------|
| `annotation not valid for an element of type Class<?>` | 1.x `@ScheduleWithTrigger(@Tag(X.class))`; 2.0 takes `@ScheduleWithTrigger(X.class)` |
| Job never fires, no error | Class is not a graph component, or `QuartzModule` is missing from `@KoraApp`, or the processor is not on the build |
| `No component found` for `Trigger` | No `@Tag(X.class) Trigger` in the graph for the class passed to `@ScheduleWithTrigger` |
| KSP: *Suspend methods are not supported* | Drop `suspend`; scheduled contracts are synchronous |
| Job runs on the wrong engine | `ScheduleWithCron` imported from `...scheduling.jdk.annotation` instead of `...scheduling.quartz` |
| Thread-count / property settings ignored | Config sits at the 1.x root `quartz { }` instead of `scheduling.quartz.properties { }` |
| Shutdown blocks for a long time | `waitForJobComplete` defaults to `true`; the job runs to completion — bound the body |
| Job keeps running after shutdown returns | `waitForJobComplete = false` does not interrupt anything |
| No `scheduling.job.duration` metric | `scheduling.telemetry.metrics.enabled` defaults to **false** in 2.0 |
| `@PersistJobDataAfterExecution` loses state on restart | Default store is `RAMJobStore`; persistence needs a JDBC JobStore |
| Wrong fire time | Quartz uses the JVM default time zone; there is no time-zone attribute on the annotation |

---

## Related skills

- [kora-aop-scheduling-jdk](../kora-aop-scheduling-jdk/SKILL.md) — fixed rate/delay/once and JDK cron
- [kora-aop-logging](../kora-aop-logging/SKILL.md) — `@Log` on a scheduled method (that one *does* need `open`)
- [kora-config-hocon](../kora-config-hocon/SKILL.md) — typed config for externalised cron
- [kora-telemetry-metrics](../kora-telemetry-metrics/SKILL.md) — enabling the metrics that scheduling reports
