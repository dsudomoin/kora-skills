# Quartz Scheduling Reference (Kora 2.0)

**Artifact:** `io.koraframework:scheduling-quartz`
**Module:** `io.koraframework.scheduling.quartz.QuartzModule` (extends `io.koraframework.scheduling.common.SchedulingModule`)
**Annotation package:** `io.koraframework.scheduling.quartz` — flat, no `.annotation` segment
**Quartz:** `org.quartz-scheduler:quartz:2.5.2`

## Contents

- [Module contents](#module-contents)
- [Annotations](#annotations)
- [Generated code](#generated-code)
- [The JobExecutionContext argument](#the-jobexecutioncontext-argument)
- [Contract rules](#contract-rules)
- [Thread model](#thread-model)
- [Error propagation](#error-propagation)
- [Cron expression reference](#cron-expression-reference)
- [Migrating from Kora 1.x](#migrating-from-kora-1x)
- [See also](#see-also)

---

## Module contents

`QuartzModule` contributes five factory methods:

| Component | Notes |
|---|---|
| `@Tag(QuartzModule.class) Properties` | merged Quartz properties, read from `scheduling.quartz.properties` |
| `SchedulingQuartzConfig` | read from `scheduling.quartz` |
| `KoraQuartzJobFactory` | resolves `KoraQuartzJob` instances out of the graph by class |
| `@Root KoraQuartzScheduler` | `Wrapped<org.quartz.Scheduler>` + `Lifecycle`; starts/stops the scheduler |
| `@Root KoraQuartzJobRegistrar` | `Lifecycle` + `RefreshListener`; registers jobs and triggers |

Because `KoraQuartzScheduler` is `Wrapped<Scheduler>`, **`org.quartz.Scheduler` is injectable**
into any component — useful for `standby()`, `pauseAll()`, `triggerJob(...)` from an admin
endpoint. `SchedulingModule` additionally supplies `SchedulingTelemetryConfig` and a
`@DefaultComponent SchedulingTelemetryFactory`.

Consumed transitively via `api`: Quartz 2.5.2. Kora's `scheduling-quartz/build.gradle` excludes
`com.mchange:c3p0`, `com.mchange:mchange-commons-java`, `com.zaxxer:HikariCP-java7` and
`org.slf4j:slf4j-api` from it. Quartz's own POM already marks `c3p0` and `HikariCP` as
`provided`, so neither connection pool reaches an application classpath — that matters only for
a [JDBC JobStore](scheduling-config-reference.md#persistence-jdbc-jobstore).

---

## Annotations

### `@ScheduleWithCron`

```java
public @interface ScheduleWithCron {
    String value() default "";      // cron expression
    String identity() default "";   // Quartz TriggerBuilder.withIdentity(...)
    String config() default "";     // config path holding the cron
}
```

```java
@Component
public final class Reports {

    @ScheduleWithCron("0 0 3 * * ?")
    void nightly() { }

    @ScheduleWithCron(value = "0 0 * * * ?", identity = "hourly-check")
    void hourly() { }

    @ScheduleWithCron(config = "jobs.weekly")
    void weekly() { }
}
```

`identity` defaults to `<fully.qualified.Class>#<method>`. Two jobs that both leave `identity`
empty never collide, because the default already carries the class and method.

**`value` / `config` interaction**, exactly as the generator builds it:

| `value` | `config` | Behaviour |
|---|---|---|
| set | empty | cron is baked into the generated trigger |
| empty | empty | Java: compile error *"Quartz `@ScheduleWithCron` on '…' has no cron source"*. **Kotlin: no compile error** — the KSP generator has no such guard and emits `cronSchedule("")`, which blows up at graph init instead |
| set | set | config node wins; when the node is absent the `value` is used |
| empty | set | config node is **required** — an absent one throws `ConfigValueException` during graph build |

The config node may be a bare string or an object with a `cron` field; see
[per-job configuration](scheduling-config-reference.md#per-job-configuration).

### `@ScheduleWithTrigger`

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.CLASS)
public @interface ScheduleWithTrigger {
    Class<?> value();               // tag selecting the org.quartz.Trigger
}
```

The class is used as a **`@Tag`** on the `org.quartz.Trigger` parameter of the generated factory
method. Supply the trigger anywhere in the graph under the same tag:

```java
@KoraApp
public interface Application extends QuartzModule {

    @Tag(RapidCheckJob.class)
    default Trigger rapidCheckTrigger() {
        return TriggerBuilder.newTrigger()
            .withIdentity("rapidCheck")
            .startNow()
            .withSchedule(SimpleScheduleBuilder.simpleSchedule()
                .withIntervalInSeconds(5)
                .repeatForever())
            .build();
    }
}

@Component
public final class RapidCheckJob {

    @ScheduleWithTrigger(RapidCheckJob.class)
    void checkStatus() { }
}
```

```kotlin
@Component
class RapidCheckJob {

    @ScheduleWithTrigger(RapidCheckJob::class)
    fun checkStatus() { }
}
```

A missing trigger is a normal DI failure ("no component found" for the tagged `Trigger`), not a
silent no-op. The tag class is arbitrary — tagging with the job class is only a convention that
keeps the two ends readable.

A `KoraQuartzJob` can carry several triggers (`KoraQuartzJob.getTriggers()` returns a list), but
the generated code always builds a single-trigger job; multiple triggers per method are not
expressible through the annotations.

### `@DisallowConcurrentExecution` and `@PersistJobDataAfterExecution`

Both are **Kora's own** annotations in `io.koraframework.scheduling.quartz`, `@Target(METHOD)`,
`@Retention(RUNTIME)`. They carry no attributes. The generator also honours the Quartz originals
— but the placement rules differ and are not interchangeable:

| Annotation | Where the generator looks |
|---|---|
| `io.koraframework.scheduling.quartz.DisallowConcurrentExecution` | on the **method** |
| `org.quartz.DisallowConcurrentExecution` | on the **class** |
| `io.koraframework.scheduling.quartz.PersistJobDataAfterExecution` | on the **method** |
| `org.quartz.PersistJobDataAfterExecution` | on the **class** |

Either form results in the corresponding `org.quartz.*` annotation being placed on the generated
job class. Neither annotation can be moved to the other's position: the Quartz originals are
`@Target(TYPE)` and the Kora ones are `@Target(METHOD)`, so a misplaced one is a compile error
rather than a silently ignored annotation.

```java
@Component
public final class HourlyJob {

    @DisallowConcurrentExecution                 // Kora's, on the method
    @ScheduleWithCron("0 0 * * * ?")
    void hourly() { }
}
```

```java
@org.quartz.DisallowConcurrentExecution          // Quartz's, on the class
@Component
public final class AllJobsSerial {

    @ScheduleWithCron("0 0 * * * ?")
    void hourly() { }
}
```

`@PersistJobDataAfterExecution` asks Quartz to re-store the `JobDataMap` after each execution.
It only means something with a persistent job store — with the default `RAMJobStore` the map
dies with the process. Kora never reads or writes the map for you: the job body must do that
through the `JobExecutionContext` argument. Pair it with `@DisallowConcurrentExecution` to avoid
lost updates.

---

## Generated code

Per annotated class, in the class's own package:

| Generated type | Shape |
|---|---|
| `$<Class>_SchedulingModule` | `@Module` interface, one factory method per scheduled method |
| `$<Class>_<method>_Job` | `public final class … extends KoraQuartzJob` |
| a `*CronConfig` interface | only for `@ScheduleWithCron(config = "…")`; `@ConfigMapper`, extends `SchedulingJobConfig` |

The factory method takes `SchedulingTelemetryFactory`, your component, and — for
`@ScheduleWithTrigger` — the tagged `org.quartz.Trigger`. `KoraQuartzJobRegistrar` then
registers each job as a durable Quartz `JobDetail` identified by the **generated job class's
canonical name**, and reconciles triggers: triggers already stored for that job are compared
(cron expression, start/end time, simple-trigger repeat count and interval) and are replaced or
unscheduled to match the current set. This also runs on `graphRefreshed()`.

Consequences worth knowing:

- Trigger edits made directly in a persistent job store are reverted for Kora-generated jobs at
  the next startup or graph refresh.
- `KoraQuartzJobFactory` maps job classes to graph instances and falls back to Quartz's
  `PropertySettingJobFactory` for unknown classes. Generated job classes have only a three-arg
  constructor, so a node cannot instantiate a job class it does not itself carry — in a cluster
  every node must run the same build.
- Two `KoraQuartzJob` components of the same class fail fast at factory construction:
  *"Duplicate Quartz job class registered: …"*.

---

## The `JobExecutionContext` argument

A scheduled method may declare a single `org.quartz.JobExecutionContext` parameter. The
generator emits `object::method` (Java) / `{ ctx -> target.method(ctx) }` (Kotlin) instead of the
no-arg form.

```java
@Component
public final class StatefulJob {

    @PersistJobDataAfterExecution
    @DisallowConcurrentExecution
    @ScheduleWithCron("0 */10 * * * ?")
    void process(JobExecutionContext ctx) {
        var data = ctx.getJobDetail().getJobDataMap();
        int cursor = data.getInt("cursor");
        // ... work ...
        data.put("cursor", cursor + processed);   // re-stored thanks to @PersistJobDataAfterExecution
    }
}
```

Useful accessors: `ctx.getFireTime()`, `ctx.getScheduledFireTime()`,
`ctx.getPreviousFireTime()`, `ctx.getNextFireTime()`, `ctx.getRefireCount()`,
`ctx.getTrigger()`, `ctx.getMergedJobDataMap()`.

---

## Contract rules

- Return `void` / `Unit`. The generated wrapper is a `Consumer<JobExecutionContext>`; any return
  value is silently discarded.
- Zero arguments, or exactly one `JobExecutionContext`.
- No `suspend`. KSP rejects it before generation:

  ```
  Suspend methods are not supported by the scheduling generator.
  … For structured concurrency, enable Java preview features with --enable-preview
  and use StructuredTaskScope …
  Fix: remove suspend from the function.
  ```

- No `Mono`, `Flux` or `CompletionStage` — they are not Kora 2.0 contracts anywhere.
- No `Context`: the type was removed from the framework. Fire-time data comes from
  `JobExecutionContext`; correlation data from the Kora `MDC` that `KoraQuartzJob` installs.
- Only member functions. KSP rejects a top-level or local function: *"`@ScheduleWithTrigger` can
  be applied only to member functions."*
- The enclosing class must be a graph component; the generated module takes it as a dependency.
- The class does **not** need to be non-`final` / `open`. The generated job calls the method on a
  held reference rather than subclassing it — the migrated Java example is
  `public final class TriggerScheduler`, and Kora's own KSP tests compile a plain Kotlin `class`.
  `open` becomes necessary only when a method-wrapping aspect (`@Log`, `@Retryable`, `@Timeout`,
  `@Cacheable`) is stacked on the same class.

---

## Thread model

`KoraQuartzScheduler.init()` builds a plain `StdSchedulerFactory` from the merged properties. No
executor is injected, so job execution uses whatever `org.quartz.threadPool.class` resolves to —
by default `org.quartz.simpl.SimpleThreadPool` with `threadCount = 10` and `threadPriority = 5`.

- Those are **platform** threads, named `kora-quartz-scheduler_Worker-N`, and non-daemon
  (`makeThreadsDaemons` defaults to `false`).
- Kora 2.0 running synchronous contracts on virtual threads does not apply here — a Quartz job
  body runs on a Quartz worker.
- `threadCount` caps concurrent executions across every Quartz job in the application. Fewer
  threads than simultaneously-due jobs means delayed firings and misfires.

`KoraQuartzJob.execute` is `final`. It binds, per execution: a fresh Kora
`io.koraframework.logging.common.MDC`, a root OpenTelemetry context, and the scheduling
`Observation` — so `@Log`-style MDC keys, tracing and the scheduling telemetry all work inside
the body despite the foreign thread.

---

## Error propagation

```java
// KoraQuartzJob.execute, simplified
observation.observeRun();
try {
    this.job.accept(jobExecutionContext);
} catch (Throwable e) {
    observation.observeError(e);
    throw e;                      // rethrown into Quartz
} finally {
    observation.end();
}
```

The exception reaches Quartz, which applies the trigger's misfire/refire policy and logs it
through its own SLF4J logger. Kora additionally logs it at WARN — *"Scheduled Job execution
failed with error"* with `exceptionType` and `exceptionMessage` — when
`scheduling.telemetry.logging.enabled = true` (it is `false` by default).

Catch inside the method when the failure should not surface as a Quartz job failure:

```java
@ScheduleWithCron("0 0 * * * ?")
void hourly() {
    try {
        doWork();
    } catch (Exception e) {
        log.error("Hourly job failed", e);
    }
}
```

Startup failures are reported by `KoraQuartzJobRegistrar` as
*"Quartz job '<class>' failed to start: …; check job triggers and Quartz scheduler
configuration"*.

---

## Cron expression reference

Quartz's own parser (`CronScheduleBuilder.cronSchedule`), documented on
`@ScheduleWithCron`:

```
┌───────────── second (0-59)
│ ┌───────────── minute (0-59)
│ │ ┌───────────── hour (0-23)
│ │ │ ┌───────────── day of month (1-31)
│ │ │ │ ┌───────────── month (1-12 or JAN-DEC)
│ │ │ │ │ ┌───────────── day of week (1-7 or SUN-SAT)
│ │ │ │ │ │ ┌───────────── year (optional, 1970-2099)
│ │ │ │ │ │ │
* * * ? * * *
```

Six or seven fields. Day-of-month and day-of-week are mutually exclusive — one of the two must
be `?`. There is no time-zone attribute on the annotation; Quartz uses the JVM default zone, so
set `-Duser.timezone=…` or `TZ` explicitly if the schedule is timezone-sensitive.

> The JDK scheduler's `io.koraframework.scheduling.jdk.annotation.ScheduleWithCron` uses Kora's
> own `CronExpression` and accepts **five**, six or seven fields. A 5-field expression that
> works there is rejected by Quartz.

### Special characters

| Char | Meaning | Example |
|------|---------|---------|
| `*` | all values | `*` in minute = every minute |
| `?` | no specific value | `0 0 10 ? * MON` |
| `-` | range | `MON-FRI` |
| `,` | list | `MON,WED,FRI` |
| `/` | step | `*/10` = every 10 |
| `L` | last | `L` in day-of-month = last day |
| `W` | nearest weekday | `1W` = first weekday of the month |
| `#` | nth weekday | `6#2` = second Friday (day-of-week is 1-7 = SUN-SAT, so 6 = FRI) |

### Common expressions

| Expression | Description |
|------------|-------------|
| `* * * ? * * *` | every second |
| `0 * * * * ?` | every minute |
| `0 */10 * * * ?` | every 10 minutes |
| `0 0 * * * ?` | every hour at :00 |
| `0 0 8-17 * * ?` | hourly from 08:00 to 17:00 |
| `0 0 9 ? * MON-FRI` | 09:00 on weekdays |
| `0 0 0 * * ?` | midnight daily |
| `0 0 0 L * ?` | last day of the month at midnight |
| `0 0 0 1W * ?` | first weekday of the month at midnight |
| `0 0 0 ? * 6#2` | second Friday of the month at midnight |
| `0 0 0 25 12 ?` | every 25 December at midnight |

---

## Migrating from Kora 1.x

| 1.x | 2.0 |
|---|---|
| `ru.tinkoff.kora.scheduling.quartz.*` | `io.koraframework.scheduling.quartz.*` |
| `ru.tinkoff.kora:scheduling-quartz` | `io.koraframework:scheduling-quartz` |
| `ru.tinkoff.kora:kora-parent` BOM | `io.koraframework:kora-bom` |
| `@ScheduleWithTrigger(@Tag(MyJob.class))` | **`@ScheduleWithTrigger(MyJob.class)`** |
| `@ScheduleWithTrigger(Tag(MyJob::class))` | **`@ScheduleWithTrigger(MyJob::class)`** |
| root `quartz { "org.quartz.*" }` | `scheduling.quartz.properties { "org.quartz.*" }` |
| `scheduling.waitForJobComplete` | `scheduling.quartz.waitForJobComplete` (default flipped to `true`) |
| `ru.tinkoff.kora.common.Component` / `Tag` | `io.koraframework.common.annotation.Component` / `Tag` |
| `Thread.currentThread().isInterrupted()` shutdown checks | dead code — see [graceful-shutdown-reference.md](graceful-shutdown-reference.md) |
| `suspend fun` scheduled method | rejected by KSP; make it a plain function |

The nested-`@Tag` form is a hard compile error in 2.0 —
`error: annotation not valid for an element of type Class<?>` — because the attribute type is
`Class<?>`. The config renames are **silent**: unknown HOCON keys are ignored without a warning.

---

## See also

- [scheduling-config-reference.md](scheduling-config-reference.md) — config keys, telemetry, JDBC JobStore, clustering
- [graceful-shutdown-reference.md](graceful-shutdown-reference.md) — shutdown semantics
- [kora-aop-scheduling-jdk](../../kora-aop-scheduling-jdk/SKILL.md) — the JDK scheduler
- [Quartz cron trigger tutorial](https://www.quartz-scheduler.org/documentation/quartz-2.3.0/tutorials/crontrigger.html) — upstream cron grammar
