---
name: kora-aop-scheduling-db
description: "Database-backed, cluster-wide scheduled jobs in Kora 2.x on db-scheduler — @ScheduleWithCron, @ScheduleWithFixedDelay, @ScheduleOnce from io.koraframework.scheduling.db.scheduler.annotation via DbSchedulerModule (artifact io.koraframework:scheduling-db-scheduler). Each firing runs on one replica and survives restarts in one table (default kora_scheduling_db_jobs). Covers the mandatory @Root starter for DbSchedulerWrapper, scheduling.dbScheduler keys (executionParallelism, polling FETCH/LOCK_AND_FETCH, prefetchMode, tableName, initializeTable, shutdownWait), the bundled Flyway/Liquibase SQL and stable task names. Use for clustered jobs that must not run on every instance; for in-process timers use kora-aop-scheduling-jdk, for Quartz triggers use kora-aop-scheduling-quartz."
license: Apache-2.0
metadata:
  kora-version: "2.x"
---

# Kora AOP Scheduling (DB)

> **Kora sub-skill — obey the [kora-v2 meta rules](../../SKILL.md) on every task:** **R0** ground the workspace on Kora 2.0 refs before starting (framework source at tag `2.0.0.RC2` + `kora-examples` at `migration/2.0` + Kora 2.0 docs at koraframework.io/v2, which trail the source; 1.x `kora-docs` pages are never an authority) · **R1** read this sub-skill before writing code · **R2** Kora 2.0 APIs only — no Spring/Micronaut/Quarkus, no Kora 1.x APIs, no invented annotations or config keys · **R3** journal any incorrect Kora usage. Add comments/Javadoc only if asked.

| | |
|---|---|
| **Artifact** | `io.koraframework:scheduling-db-scheduler` (version from `io.koraframework:kora-bom`) — brings `com.github.kagkarlsson:db-scheduler:16.12.0` as `api` |
| **Module** | `io.koraframework.scheduling.db.scheduler.DbSchedulerModule` (extends `io.koraframework.scheduling.common.SchedulingModule`) |
| **Annotations** | `io.koraframework.scheduling.db.scheduler.annotation.{ScheduleWithCron, ScheduleWithFixedDelay, ScheduleOnce}` |
| **Processor** | Java `annotationProcessor "io.koraframework:annotation-processors"` · Kotlin `ksp("io.koraframework:symbol-processors")` |
| **Needs** | a `javax.sql.DataSource` in the graph — normally `JdbcDatabaseModule` ([kora-database-jdbc](../kora-database-jdbc/SKILL.md)) |
| **Config roots** | `scheduling.dbScheduler` · `scheduling.telemetry` · one arbitrary path per job via `config = "…"` |

Jobs are rows in a database table. Every replica polls the table; a due execution is picked by exactly
one replica, executed, and its next execution time is written back. A restart, a deploy or a crashed
node does not lose the schedule.

---

## Quick Start

### 1. Dependency

```groovy
// build.gradle (Java)
dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")   // koraVersion=2.0.0.RC2
    annotationProcessor "io.koraframework:annotation-processors"

    implementation "io.koraframework:scheduling-db-scheduler"
    implementation "io.koraframework:database-jdbc"
    implementation "org.postgresql:postgresql"                  // your JDBC driver
    implementation "io.koraframework:config-hocon"
    implementation "io.koraframework:logging-logback"
}
```

```kotlin
// build.gradle.kts (Kotlin)
dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))
    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")

    implementation("io.koraframework:scheduling-db-scheduler")
    implementation("io.koraframework:database-jdbc")
    implementation("org.postgresql:postgresql")
    implementation("io.koraframework:config-hocon")
    implementation("io.koraframework:logging-logback")
}
```

### 2. Plug the modules into `@KoraApp`

```java
@KoraApp
public interface Application extends
    HoconConfigModule,
    LogbackModule,
    JdbcDatabaseModule,
    DbSchedulerModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

### 3. Pull the scheduler into the graph — mandatory

The generated jobs are `@Root`, but the component that actually runs them, `DbSchedulerWrapper`, is a
plain `@DefaultComponent` that nothing depends on. Kora builds the graph from `@Root` components
outward, so **without a root that depends on `DbSchedulerWrapper` the scheduler is pruned and no job
ever runs** — no error, no log line. Add one small root component:

```java
@Root
@Component
public final class DbSchedulerStarter {

    public DbSchedulerStarter(DbSchedulerWrapper scheduler) {
    }
}
```

```kotlin
@Root
@Component
class DbSchedulerStarter(@Suppress("unused") scheduler: DbSchedulerWrapper)
```

(`@Root` and `@Component` from `io.koraframework.common.annotation`, `DbSchedulerWrapper` from
`io.koraframework.scheduling.db.scheduler`.) Verified against the KSP graph builder at master: without
the starter the generated graph contains the `DbSchedulerJob` nodes but no `DbSchedulerWrapper`.

### 4. Create the table

db-scheduler needs its table before the scheduler starts. Put the SQL in your own Flyway/Liquibase
migration, named after `scheduling.dbScheduler.tableName` (default **`kora_scheduling_db_jobs`**) —
see [Table](#table). For a local run, `scheduling.dbScheduler.initializeTable = true` creates it for
you.

### 5. Declare jobs

```java
@Component
public final class ReportJobs {

    @ScheduleWithCron(value = "0 0 * * * *", name = "hourly-report")
    void hourlyReport() {
        // runs on one replica per hour
    }
}
```

The processor emits a `$ReportJobs_SchedulingModule` with one `@Root` factory per method returning a
`DbSchedulerJob`. `DbSchedulerWrapper` collects them all at init and registers their tasks with
db-scheduler.

---

## Annotations

All three live in `io.koraframework.scheduling.db.scheduler.annotation`, target `METHOD`, and carry
`String name() default ""` and `String config() default ""`.

| Annotation | Attributes | db-scheduler task | Semantics |
|---|---|---|---|
| `@ScheduleWithCron` | `value` (cron, `""`), `name`, `config` | `RecurringTask`, `CronSchedule` | next run = next cron time after completion |
| `@ScheduleWithFixedDelay` | `initialDelay` (long, `0`), `delay` (long, `0`), `unit` (`ChronoUnit`, `MILLIS`), `name`, `config` | `RecurringTask`, `FixedDelay` | next run = completion + `delay`; `initialDelay` only for the very first execution |
| `@ScheduleOnce` | `delay` (long, `0`), `unit` (`ChronoUnit`, `MILLIS`), `name`, `config` | custom task, scheduled on startup | one execution `delay` after a startup; removed after it runs, **no retry on failure** |

There is **no fixed-rate** annotation in this module.

Either the primary attribute or `config` must be set; a `delay` of `0` or a blank `value` counts as
unset and the build fails with `Either delay() or config() annotation parameter must be provided`
(`value()` for cron).

The method rules are the ones of every Kora scheduler: a no-argument member method of a graph
component, not `private`, not `suspend`. No AOP proxy is generated, so the class may be `final` /
non-`open`.

### Task name — the persistent identity

`name` is the db-scheduler **task name**, the key of the job's row. Default: `SimpleClassName#method`
(simple name, no package). A configured `name` wins over the annotation, a blank one falls back to it.

- Renaming the class or method, or changing `name`, creates a **new** job: the old row is orphaned and
  the new one starts from scratch (including `initialDelay`). Set an explicit `name` for anything you
  may refactor.
- Two jobs with the same name — two classes called `CleanupJob` in different packages with a `run()`
  method is enough — fail startup with `IllegalStateException: Duplicate key …` from db-scheduler.

### Cron

`@ScheduleWithCron` hands the expression to db-scheduler's `CronSchedule`: **Spring-style, six fields
with seconds first** (`second minute hour day-of-month month day-of-week`), evaluated in the JVM default
time zone. This is not Kora's JDK `CronExpression` (5/6/7 fields) and not Quartz cron. An invalid
expression fails graph init. The special value `"-"` disables the job: on startup its pending execution
is removed and nothing is scheduled.

Details: [references/db-scheduler-reference.md](references/db-scheduler-reference.md).

---

## Externalized parameters (`config`)

`config = "<path>"` generates a `SchedulingJobConfig` subtype bound to that path. Annotation values are
defaults, config wins, and an attribute you leave off becomes a required key.

| Annotation | Keys under the config path |
|---|---|
| `@ScheduleWithCron` | `cron`, `name` — or set the path itself to the cron string |
| `@ScheduleWithFixedDelay` | `initialDelay`, `delay`, `name` |
| `@ScheduleOnce` | `delay`, `name` |

Each path also accepts a `telemetry { … }` block overriding `scheduling.telemetry` for that job. Use a
path outside `scheduling.dbScheduler` and `scheduling.telemetry`, e.g. `scheduling.jobs.<name>`.

---

## Module configuration

```hocon
scheduling {
  dbScheduler {
    tableName = "kora_scheduling_db_jobs"   # default
    initializeTable = false                 # default; true = create the table at startup if missing
    executionParallelism = 10               # default; max job bodies running at once on this replica
    shutdownWait = 30s                      # default
    polling {
      strategy = "FETCH"                    # default; or LOCK_AND_FETCH
      prefetchMode = "DEFAULT"              # default; or BOUNDED, BUFFERED
      interval = 10s                        # default
    }
  }
}
```

Every key has a default, so an absent `scheduling.dbScheduler` section is fine. Full reference, the
prefetch ratios and when to pick `LOCK_AND_FETCH`:
[references/db-scheduler-config-reference.md](references/db-scheduler-config-reference.md).

---

## Table

The module ships schema SQL for PostgreSQL, MySQL, MariaDB, MS SQL, Oracle and HSQL under
`db/scheduling-db/flyway/<db>/V1__create_scheduled_tasks.sql`, plus a Liquibase changelog
`db/scheduling-db/liquibase/changelog.yaml` (one `dbms`-guarded changeset per database).

**Those files create a table named `scheduled_tasks`, while Kora's default `tableName` is
`kora_scheduling_db_jobs`.** Pick one:

| Approach | What to do |
|---|---|
| Own migration (recommended) | Copy the SQL for your database into your migration with your own version number, table renamed to `kora_scheduling_db_jobs` ([asset](assets/V2__create_kora_scheduling_db_jobs.sql.template)) |
| Bundled files | Include them in Flyway/Liquibase **and** set `scheduling.dbScheduler.tableName = "scheduled_tasks"`. With Flyway, an extra location holding its own `V1__` clashes with your `V1__` in the same schema history — prefer the copy |
| `initializeTable = true` | At startup Kora probes `select 1 from <tableName> where 1 = 0` and, if that fails, runs the bundled SQL for the detected database with the table name substituted. Fine for dev and tests; production usually owns its schema |

With `FlywayJdbcDatabaseModule`/`LiquibaseJdbcDatabaseModule` the migration runs when the
`JdbcDataSource` initialises, which is before the scheduler starts.

---

## Thread model and concurrency

- db-scheduler's own threads poll the table (`polling.interval`, default 10 s) and heartbeat.
- Job bodies run on **fresh virtual threads** named `kora-db-scheduler-N` from a
  `LimitedVirtualThreadPerTaskExecutor`; `executionParallelism` (default 10) caps how many run at once on
  one replica and is also passed to db-scheduler as its thread count.
- One execution of a task instance runs on one replica at a time. A due execution can start up to one
  polling interval late.
- A `Configurer<SchedulerBuilder>` component (from `io.koraframework.common`) is applied last to the
  `SchedulerBuilder` for anything the config does not expose.

---

## Failures and dead executions

The wrapper records the exception on the span/metric/log and **rethrows** it to db-scheduler:

- cron / fixed-delay jobs are rescheduled for their next regular time (db-scheduler
  `OnFailureReschedule`) — no immediate retry;
- `@ScheduleOnce` is removed on failure — it does not retry;
- if a replica dies mid-run, the execution stays picked until db-scheduler declares it dead (heartbeat
  every 5 min, dead after 6 missed by default) and another replica revives it.

Make job bodies idempotent: a revived execution runs the work again.

---

## Graceful shutdown

`DbSchedulerWrapper.release()` calls db-scheduler's `Scheduler.stop()`: polling stops, then running
executions get `scheduling.dbScheduler.shutdownWait` (default 30 s) to finish; after that their virtual
threads are **interrupted** and db-scheduler waits up to another `shutdownWait`. So worst case is about
2× `shutdownWait`. The wrapper depends on the jobs and the `DataSource`, so it is released before them
— a running job still has its resources while it drains. Bound long jobs (batch cap, deadline) and
honour `Thread.currentThread().isInterrupted()`.

---

## Telemetry

Shared with the other schedulers through `scheduling-common`: the same `scheduling.telemetry` block
(`logging.enabled` default `false`, `metrics.enabled` default `false`, `tracing.enabled` default
`true`), the `scheduling.job.duration` timer, span `scheduling <fqcn>#<method>` from a root context, and
per-job overrides under `<config-path>.telemetry`. The job `name` is **not** a tag; jobs are identified
by class and method. See [kora-aop-scheduling-jdk telemetry](../kora-aop-scheduling-jdk/references/jdk-scheduling-reference.md#telemetry).

---

## Common pitfalls

| Symptom | Cause / fix |
|---|---|
| Jobs never run, no error, no `SchedulingDbScheduler started` log | No `@Root` depends on `DbSchedulerWrapper`, so it was pruned — add the [starter](#3-pull-the-scheduler-into-the-graph--mandatory) |
| `No component found` for `DataSource` | No JDBC module in the graph; add `JdbcDatabaseModule` (or a `@Tag(DbSchedulerWrapper.class) DataSource`) |
| App starts, logs `Unexpected error while executing OnStartup tasks. Continuing.` and SQL errors about `kora_scheduling_db_jobs`; jobs never run | Table missing, or created as `scheduled_tasks` by the bundled SQL — db-scheduler logs and carries on. Fix the table, then restart so the startup tasks are registered |
| `IllegalStateException: Duplicate key <name>` | Two jobs resolve to the same task name, or a `Configurer<SchedulerBuilder>` registers a Kora task again via `startTasks(...)` — Kora already registers every startup task itself |
| Changed `initialDelay` has no effect | It applies only when the job's row is first created |
| Longer `delay` in config has no effect until the next run | db-scheduler only moves an existing execution **earlier** on startup; cron changes are applied immediately |
| Job ran again after a deploy / new name appeared in the table | Task name changed (class/method rename) — set a stable `name` |
| `@ScheduleOnce` ran again after a restart | It is scheduled on every startup when no execution is pending; guard the work if it must happen once ever |
| Cron `0 0 3 * * ? 2027` rejected / wrong fields | db-scheduler cron is Spring-style six fields; no year field |
| Job runs on every replica | Wrong import — `@ScheduleWithCron` from `...scheduling.jdk.annotation` or `...scheduling.quartz` |

---

## Which scheduler?

Same table in all three scheduling sub-skills:

| Need | JDK | Quartz | DB (this skill) |
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

## References, assets

| File | Purpose |
|---|---|
| [references/db-scheduler-reference.md](references/db-scheduler-reference.md) | Annotations, generated code, cron, task lifecycle on restart, failures, custom `DbSchedulerJob` |
| [references/db-scheduler-config-reference.md](references/db-scheduler-config-reference.md) | Every `scheduling.dbScheduler` key, polling/prefetch, per-job config, HOCON + YAML |
| [assets/DbScheduledJobs.java.template](assets/DbScheduledJobs.java.template) | Java jobs + `@Root` starter |
| [assets/DbScheduledJobs.kt.template](assets/DbScheduledJobs.kt.template) | Kotlin jobs + `@Root` starter |
| [assets/application.conf.template](assets/application.conf.template) | `scheduling.dbScheduler`, telemetry and per-job config |
| [assets/V2__create_kora_scheduling_db_jobs.sql.template](assets/V2__create_kora_scheduling_db_jobs.sql.template) | PostgreSQL Flyway migration for the default table name |

---

## Related skills

- [kora-aop-scheduling-jdk](../kora-aop-scheduling-jdk/SKILL.md) — in-process timers and cron, every replica
- [kora-aop-scheduling-quartz](../kora-aop-scheduling-quartz/SKILL.md) — Quartz triggers, misfire policies, JDBC JobStore
- [kora-database-jdbc](../kora-database-jdbc/SKILL.md) — the `JdbcDatabaseModule` / `DataSource` the scheduler uses
- [kora-database-migration](../kora-database-migration/SKILL.md) — Flyway / Liquibase for the scheduler table
- [kora-di-runtime](../kora-di-runtime/SKILL.md) — `@Root` pruning, `Wrapped<T>`, release order
- [kora-telemetry-metrics](../kora-telemetry-metrics/SKILL.md) — the `MeterRegistry` job metrics need
