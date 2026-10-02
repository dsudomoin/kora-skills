# DB Scheduling Reference

**Artifact:** `io.koraframework:scheduling-db-scheduler` (`com.github.kagkarlsson:db-scheduler:16.12.0` as `api`)
**Module:** `io.koraframework.scheduling.db.scheduler.DbSchedulerModule`
**Annotations:** `io.koraframework.scheduling.db.scheduler.annotation.*`
**Runtime:** db-scheduler `Scheduler`, wrapped by `DbSchedulerWrapper` (`Lifecycle`, `Wrapped<Scheduler>`)

## Contents

- [What the module provides](#what-the-module-provides)
- [What the processor generates](#what-the-processor-generates)
- [Annotations](#annotations)
- [Cron syntax](#cron-syntax)
- [What happens on restart](#what-happens-on-restart)
- [Failures and dead executions](#failures-and-dead-executions)
- [Graceful shutdown](#graceful-shutdown)
- [Custom `DbSchedulerJob`](#custom-dbschedulerjob)
- [Telemetry](#telemetry)

---

## What the module provides

| Component | Factory | Notes |
|---|---|---|
| `DbSchedulerConfig` | `schedulingDbConfig` | mapped from `scheduling.dbScheduler` |
| `@Tag(DbSchedulerWrapper.class) DataSource` | `schedulingDbDataSource(DataSource)`, `@DefaultComponent` | returns the untagged `DataSource`; supply your own tagged one to use a different database |
| `DbSchedulerWrapper` | `schedulingDbScheduler(...)`, `@DefaultComponent` | takes the tagged `DataSource`, the config, `All<ValueOf<DbSchedulerJob>>` and an optional `Configurer<SchedulerBuilder>` |
| `SchedulingTelemetryFactory`, `SchedulingTelemetryConfig` | inherited from `SchedulingModule` | shared with the JDK and Quartz schedulers |

`DbSchedulerWrapper` is **not** `@Root`. Nothing in the module depends on it, so the application must
declare a `@Root` component that does — otherwise the graph builder never reaches it and the scheduler
is not created:

```java
@Root
@Component
public final class DbSchedulerStarter {

    public DbSchedulerStarter(DbSchedulerWrapper scheduler) {
    }
}
```

Injecting `com.github.kagkarlsson.scheduler.Scheduler` into a `@Root` component works as well
(`DbSchedulerWrapper` is `Wrapped<Scheduler>`), and gives you the db-scheduler client API
(`getScheduledExecutions`, `reschedule`, `cancel`) for operational tooling.

`JdbcDatabaseModule` satisfies the `DataSource` dependency: its `JdbcDataSource` is a
`Wrapped<DataSource>`.

### `init()`

1. If `initializeTable` is `true`, create `tableName` when it is missing (see
   [db-scheduler-config-reference.md](db-scheduler-config-reference.md#initializetable)).
2. Collect every `DbSchedulerJob` and take its `task()`. Tasks that implement db-scheduler's
   `OnStartup` (all three annotation kinds) are registered through `startTasks(...)`, the rest as
   plain known tasks — each task exactly once.
3. Build the `Scheduler`: `threads(executionParallelism)`, a `LimitedVirtualThreadPerTaskExecutor`
   named `kora-db-scheduler` as the executor service, `pollingInterval`, `shutdownMaxWait =
   shutdownWait`, `tableName`, and the polling strategy with the prefetch ratios.
4. Apply `Configurer<SchedulerBuilder>` if one exists — last, so it can override anything above.
5. `scheduler.start()` and log `SchedulingDbScheduler started in …`.

---

## What the processor generates

For each class holding DB-scheduled methods, one `@Module` interface `$<ClassName>_SchedulingModule`
with a `@Root` factory `$<ClassName>_<method>_Job` per method. The factory takes
`SchedulingTelemetryFactory` and `ValueOf<YourClass>` and returns a `DbSchedulerJob` — `CronJob`,
`FixedDelayJob` or `RunOnceJob` from `io.koraframework.scheduling.db.scheduler.job`. The job body is
`target.get().yourMethod()`; no AOP proxy is involved.

With `config = "<path>"` it also generates `$<ClassName>_<method>_Config extends SchedulingJobConfig`
(`@ConfigMapper`) with `name()` (nullable) plus the timing keys, and a factory binding it to `<path>`.
Annotation values become `default` methods; an unset one becomes abstract, i.e. a required key.

---

## Annotations

### `@ScheduleWithCron`

| Attribute | Type | Default |
|---|---|---|
| `value` | `String` | `""` |
| `name` | `String` | `""` |
| `config` | `String` | `""` |

`value` is required without `config`. With `config`, the path may be a plain string (the cron) or an
object with `cron`, `name` and `telemetry`; a non-blank `value` is the default `cron` and is also used
when the path is absent.

```java
@ScheduleWithCron(value = "0 0 2 * * *", name = "nightly-cleanup", config = "scheduling.jobs.cleanup")
void cleanup() { }
```

### `@ScheduleWithFixedDelay`

| Attribute | Type | Default |
|---|---|---|
| `initialDelay` | `long` | `0` |
| `delay` | `long` | `0` |
| `unit` | `ChronoUnit` | `MILLIS` |
| `name` | `String` | `""` |
| `config` | `String` | `""` |

The next execution is `delay` after the previous one **completed**. `initialDelay` is added only when
the job's row is first created — see [What happens on restart](#what-happens-on-restart).

```java
@ScheduleWithFixedDelay(initialDelay = 1, delay = 5, unit = ChronoUnit.MINUTES, name = "outbox-relay")
void relayOutbox() { }
```

### `@ScheduleOnce`

| Attribute | Type | Default |
|---|---|---|
| `delay` | `long` | `0` |
| `unit` | `ChronoUnit` | `MILLIS` |
| `name` | `String` | `""` |
| `config` | `String` | `""` |

Registered as a db-scheduler startup task with instance id = task name. On each startup of any
replica, if no execution of it is pending, one is created at `now + delay`; concurrent startups create
only one. After it runs — successfully or not — the row is removed. Consequences:

- one run per "startup wave", not one run ever: a restart after the run schedules it again;
- a failure is not retried.

For a one-time data fix that must never repeat, record completion in your own table and make the method
a no-op once done.

### Task names

The task name is `name` from config, else the annotation `name`, else `SimpleClassName#method`. It is
the job's key in the table. Duplicate names make db-scheduler fail building its task map
(`IllegalStateException: Duplicate key …`). A renamed task leaves the old row behind as an unresolved
execution (db-scheduler logs `Found execution with unknown task-name …`); db-scheduler deletes
unresolved rows after 14 days by default.

---

## Cron syntax

db-scheduler `CronSchedule(pattern)` — cron-utils with the Spring 5.3 definition, JVM default zone:

```
┌───────────── second (0-59)
│ ┌───────────── minute (0-59)
│ │ ┌───────────── hour (0-23)
│ │ │ ┌───────────── day of the month (1-31)
│ │ │ │ ┌───────────── month (1-12 or JAN-DEC)
│ │ │ │ │ ┌───────────── day of the week (0-7 or MON-SUN)
* * * * * *
```

Six fields, seconds first, no year. `"-"` is db-scheduler's *disabled* schedule: on startup the pending
execution is cancelled and nothing new is scheduled — handy as a config override:

```hocon
scheduling.jobs.cleanup = "-"   # switch the job off without a code change
```

Do not reuse a JDK (`5/7`-field) or Quartz (`L W #`, year) expression blindly — check it against the
Spring format.

---

## What happens on restart

For cron and fixed-delay jobs db-scheduler runs its recurring-startup check on every start:

| Row state | Effect |
|---|---|
| no row | create one at `now + initialDelay` (fixed delay) or the next cron time |
| row exists, cron changed | if the stored time differs from the new next cron time by more than 1 s, **reschedule** to the new time |
| row exists, fixed delay shortened | if the stored time is later than `now + delay`, move it **earlier** |
| row exists, fixed delay lengthened | kept; the new delay applies after the next run |
| stored time within 10 s of now | left alone |
| schedule disabled (`"-"`) | row removed |

`initialDelay` therefore affects only the first deployment of a job name.

---

## Failures and dead executions

`AbstractJob.runJob()` installs a fresh MDC and root OpenTelemetry context, observes the run, and
**rethrows** any exception after recording it. db-scheduler then applies the task's failure handler
and logs the failure (WARN by default):

| Job | On failure |
|---|---|
| `@ScheduleWithCron`, `@ScheduleWithFixedDelay` | `OnFailureReschedule` — next regular execution time, `consecutive_failures` incremented |
| `@ScheduleOnce` | execution removed — no retry |

Each replica heartbeats its picked executions (every 5 min by default). An execution whose owner missed
6 heartbeats is considered dead; recurring tasks are revived and run again elsewhere. Idempotent job
bodies make that safe.

---

## Graceful shutdown

`DbSchedulerWrapper.release()` → `Scheduler.stop()`:

1. stop the polling thread;
2. `shutdown()` the job executor and wait `shutdownWait` for running executions;
3. if they are still running: `shutdownNow()` — the job's virtual thread is **interrupted** — and wait
   up to another `shutdownWait`;
4. stop heartbeating.

db-scheduler logs `Letting running executions finish. Will wait up to 2x…`. The wrapper is released
before the jobs and the `DataSource` it depends on, so a draining run keeps its database pool. An
interrupted execution that did not complete is picked up by the dead-execution check on another
replica later.

---

## Custom `DbSchedulerJob`

For task data, custom failure handling or db-scheduler features the annotations do not expose,
implement `io.koraframework.scheduling.db.scheduler.job.DbSchedulerJob` (single method
`Task<?> task()`) as a `@Root` component. The wrapper registers it with the annotated jobs; if the task
implements `OnStartup` it goes through `startTasks`. Keep the task name stable, and do not register the
same task again from a `Configurer<SchedulerBuilder>`.

---

## Telemetry

Identical model to the JDK scheduler (`scheduling-common`): `scheduling.telemetry` toggles, the
`scheduling.job.duration` timer, span `scheduling <fqcn>#<method>` (kind `INTERNAL`, root context), log
lines `Scheduled Job execution started/completed/failed with error`, per-job overrides under
`<config-path>.telemetry`. Jobs declared without `config` get no `system.config` tag (Java and
Kotlin); with `config` it carries the path. Tags and attributes identify the class and method, not
the task `name`.

Full key list and metric/span details:
[kora-aop-scheduling-jdk telemetry](../../kora-aop-scheduling-jdk/references/jdk-scheduling-reference.md#telemetry).

---

## See Also

- [db-scheduler-config-reference.md](db-scheduler-config-reference.md) — every `scheduling.dbScheduler` key
- [kora-aop-scheduling-jdk](../../kora-aop-scheduling-jdk/SKILL.md) — in-process scheduler
- [kora-aop-scheduling-quartz](../../kora-aop-scheduling-quartz/SKILL.md) — Quartz scheduler
- [kora-database-migration](../../kora-database-migration/SKILL.md) — Flyway / Liquibase for the table
