# Quartz Scheduling Configuration Reference (Kora 2.0)

Configuration for `io.koraframework:scheduling-quartz`. For fixed-rate / fixed-delay / one-shot
timers and the JDK cron see [kora-aop-scheduling-jdk](../../kora-aop-scheduling-jdk/SKILL.md).

## Contents

- [Key map](#key-map)
- [Module setup](#module-setup)
- [Quartz properties](#quartz-properties)
- [Per-job configuration](#per-job-configuration)
- [Telemetry](#telemetry)
- [Shutdown](#shutdown)
- [Persistence (JDBC JobStore)](#persistence-jdbc-jobstore)
- [Clustering](#clustering)
- [Keys removed in 2.0](#keys-removed-in-20)
- [See also](#see-also)

---

## Key map

Every key below is read by a specific factory method — nothing else under `scheduling` is
consumed by this module.

| Key | Type | Default | Read by |
|---|---|---|---|
| `scheduling.quartz.properties` | object of `org.quartz.*` strings | Quartz jar defaults | `QuartzModule.quartzProperties` |
| `scheduling.quartz.waitForJobComplete` | boolean | **`true`** | `SchedulingQuartzConfig` |
| `scheduling.telemetry.logging.enabled` | boolean | **`false`** | `SchedulingModule.schedulingTelemetryConfig` |
| `scheduling.telemetry.metrics.enabled` | boolean | **`false`** | same |
| `scheduling.telemetry.metrics.slo` | duration array | 14 buckets, 1 ms … 90 s | same |
| `scheduling.telemetry.metrics.tags` | object | `{}` | same |
| `scheduling.telemetry.tracing.enabled` | boolean | **`true`** | same |
| `scheduling.telemetry.tracing.attributes` | object | `{}` | same |
| `<job config path>.cron` | string | annotation `value()` when present | generated `*CronConfig` |
| `<job config path>.telemetry.*` | object | falls back to `scheduling.telemetry` | generated `*CronConfig` |

Unknown keys anywhere in this tree are ignored without a warning. A typo therefore looks like a
clean startup on defaults.

---

## Module setup

```java
@KoraApp
public interface Application extends
    HoconConfigModule,      // or YamlConfigModule
    LogbackModule,
    QuartzModule {
}
```

```groovy
dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")   // koraVersion=2.0.0.RC2
    annotationProcessor "io.koraframework:annotation-processors" // Kotlin: ksp "io.koraframework:symbol-processors"

    implementation "io.koraframework:scheduling-quartz"
    implementation "io.koraframework:config-hocon"
}
```

---

## Quartz properties

Raw `org.quartz.*` properties go under **`scheduling.quartz.properties`** and are handed to
`StdSchedulerFactory.initialize(Properties)` unchanged.

### HOCON

```hocon
scheduling.quartz.properties {
  "org.quartz.scheduler.instanceName" = "my-service-scheduler"
  "org.quartz.threadPool.threadCount" = "20"
  "org.quartz.threadPool.threadPriority" = "5"
  "org.quartz.jobStore.misfireThreshold" = "60000"
}
```

### YAML

```yaml
scheduling:
  quartz:
    properties:
      org.quartz.scheduler.instanceName: "my-service-scheduler"
      org.quartz.threadPool.threadCount: "20"
      org.quartz.threadPool.threadPriority: "5"
      org.quartz.jobStore.misfireThreshold: "60000"
```

Values are Quartz properties — keep them **strings**, including numbers.

### Resolution order

`QuartzModule.quartzProperties` builds a defaults set, then lets your config override it:

1. everything in `org/quartz/quartz.properties` bundled inside the Quartz jar;
2. `org.quartz.scheduler.instanceName = kora-quartz-scheduler` (overriding Quartz's
   `DefaultQuartzScheduler`);
3. `org.quartz.scheduler.instanceId = AUTO`;
4. every key present in `scheduling.quartz.properties` wins over 1–3.

Effective defaults inherited from the Quartz jar at 2.5.2:

| Property | Default |
|---|---|
| `org.quartz.threadPool.class` | `org.quartz.simpl.SimpleThreadPool` |
| `org.quartz.threadPool.threadCount` | `10` |
| `org.quartz.threadPool.threadPriority` | `5` |
| `org.quartz.threadPool.threadsInheritContextClassLoaderOfInitializingThread` | `true` |
| `org.quartz.jobStore.class` | `org.quartz.simpl.RAMJobStore` |
| `org.quartz.jobStore.misfireThreshold` | `60000` |
| `org.quartz.scheduler.rmi.export` / `.proxy` | `false` |
| `org.quartz.scheduler.wrapJobExecutionInUserTransaction` | `false` |

`threadCount` is the ceiling on concurrent job executions across the whole application — see
[the thread model](quartz-scheduling-reference.md#thread-model).

---

## Per-job configuration

`@ScheduleWithCron(config = "<path>")` generates a `@ConfigMapper` interface extending
`SchedulingJobConfig`, bound to `<path>`. The node may be a **string** or an **object**.

```java
@ScheduleWithCron(config = "jobs.nightly")
void nightlyReport() { }

@ScheduleWithCron(config = "jobs.hourly")
void hourlyCheck() { }
```

```hocon
jobs {
  # string form — cron only
  nightly = "0 0 3 * * ?"

  # object form — cron plus per-job telemetry
  hourly {
    cron = "0 0 * * * ?"
    telemetry {
      logging.enabled = true
      metrics.enabled = true
      metrics.tags { component = "billing" }
      tracing.enabled = false
    }
  }
}
```

```yaml
jobs:
  nightly: "0 0 3 * * ?"
  hourly:
    cron: "0 0 * * * ?"
    telemetry:
      logging:
        enabled: true
      metrics:
        enabled: true
        tags:
          component: "billing"
      tracing:
        enabled: false
```

- If the annotation also carries `value`, that expression is the fallback used when the node is
  absent; a present node always wins.
- If the annotation carries no `value`, the node is required — an absent one raises
  `ConfigValueException` during graph build.
- Every field under `telemetry` is nullable and falls back to `scheduling.telemetry`; an omitted
  `telemetry` block is fine.
- **Per-job telemetry is only available through `config`.** Jobs declared with an inline cron or
  with `@ScheduleWithTrigger` get the global `scheduling.telemetry` settings; the generated code
  passes `null` for the per-job config in those branches.

---

## Telemetry

Defaults come from `io.koraframework.telemetry.common.TelemetryConfig`: **logging `false`,
metrics `false`, tracing `true`**. Nothing is reported until you turn it on.

```hocon
scheduling.telemetry {
  logging.enabled = true
  metrics {
    enabled = true
    slo = ["100ms", "500ms", "1s", "10s", "1m"]
    tags { service = "billing" }
  }
  tracing {
    enabled = true
    attributes { deployment = "prod" }
  }
}
```

### Metrics

One Micrometer **`Timer`** named **`scheduling.job.duration`**, recorded once per execution.

| Tag | Value |
|---|---|
| `code.function.name` | `<fully.qualified.Class>#<method>` |
| `system.name.simple` | `<SimpleClass>#<method>` |
| `system.name.canonical` | `<fully.qualified.Class>#<method>` |
| `error.type` | canonical exception class name, or `""` on success |
| `system.config` | the `config` path — only present for `@ScheduleWithCron(config = "…")` |
| … | everything in `scheduling.telemetry.metrics.tags` (or the per-job override) |

Buckets come from `metrics.slo`; the default array is 1, 10, 50, 100, 200, 500, 1000, 2000,
5000, 10000, 20000, 30000, 60000, 90000 ms.

Metrics also need a `MeterRegistry` in the graph (`micrometer-module`) — with the flag on but no
registry the telemetry degrades to a no-op.

### Tracing

Span name `scheduling <fully.qualified.Class>#<method>`, with attributes
`code.function.name`, `system.name.simple`, `system.name.canonical`, optionally `system.config`,
plus everything in `tracing.attributes`. Requires an OpenTelemetry `Tracer` in the graph.

### Logging

| Event | Level | Message |
|---|---|---|
| start | DEBUG | `Scheduled Job execution started` |
| success | INFO | `Scheduled Job execution completed` |
| failure | WARN | `Scheduled Job execution failed with error` |

Key-values: `jobClass`, `jobMethod`, `duration` (ms), plus `jobConfigPath` when the job uses
`config`, plus `exceptionType` / `exceptionMessage` on failure.

The **logger name is `<fully.qualified.Class>#<method>`**. Logback splits the hierarchy on `.`,
so `Foo#nightly` is one segment: a `logging.levels` entry for the class alone does not match it.
Target the package, or the exact `Class#method` string:

```hocon
logging.levels {
  "com.example.jobs" = "DEBUG"                       # matches every job in the package
  "com.example.jobs.Reports#nightly" = "DEBUG"       # matches exactly one job
}
```

---

## Shutdown

| Key | Type | Default |
|---|---|---|
| `scheduling.quartz.waitForJobComplete` | boolean | **`true`** |

`KoraQuartzScheduler.release()` calls `Scheduler.shutdown(waitForJobComplete)`.

- `true` — `release()` blocks until running jobs finish.
- `false` — `release()` returns immediately. It does **not** cancel or interrupt anything; the
  non-daemon Quartz worker keeps running.

Neither value interrupts a running job. See
[graceful-shutdown-reference.md](graceful-shutdown-reference.md).

---

## Persistence (JDBC JobStore)

Kora passes the properties straight to `StdSchedulerFactory`, so any Quartz job store is
configurable — but Kora supplies **nothing** beyond that. A JDBC job store is entirely the
application's responsibility:

1. **A JDBC driver** on the runtime classpath (`org.postgresql:postgresql`, …). Kora's own
   `database-jdbc` datasource is *not* wired into Quartz; Quartz manages its own connections.
2. **A Quartz connection provider.** Quartz's `HikariCpPoolingConnectionProvider` needs
   `com.zaxxer:HikariCP` and `C3p0PoolingConnectionProvider` needs `com.mchange:c3p0`. Both are
   `provided` scope in the Quartz POM *and* excluded by `scheduling-quartz/build.gradle`, so
   neither is on the classpath until the application adds it.
3. **The `QRTZ_*` tables**, created from Quartz's DDL for the database (`tables_postgres.sql`
   and friends, shipped in the Quartz distribution). Kora runs no migration for them — add them
   to your Flyway/Liquibase changelog, or apply them out of band.

```groovy
dependencies {
    implementation "io.koraframework:scheduling-quartz"
    runtimeOnly    "org.postgresql:postgresql:42.7.13"
    // Quartz 2.5.2 compiles HikariCpPoolingConnectionProvider against HikariCP 5.0.1;
    // pin whatever line you have verified against it.
    implementation "com.zaxxer:HikariCP:5.0.1"
}
```

```hocon
scheduling.quartz.properties {
  "org.quartz.jobStore.class"                = "org.quartz.impl.jdbcjobstore.JobStoreTX"
  "org.quartz.jobStore.driverDelegateClass"  = "org.quartz.impl.jdbcjobstore.PostgreSQLDelegate"
  "org.quartz.jobStore.tablePrefix"          = "QRTZ_"
  "org.quartz.jobStore.dataSource"           = "quartzDs"

  "org.quartz.dataSource.quartzDs.provider"  = "hikaricp"
  "org.quartz.dataSource.quartzDs.driver"    = "org.postgresql.Driver"
  "org.quartz.dataSource.quartzDs.URL"       = ${DB_URL}
  "org.quartz.dataSource.quartzDs.user"      = ${DB_USER}
  "org.quartz.dataSource.quartzDs.password"  = ${DB_PASSWORD}
  "org.quartz.dataSource.quartzDs.maxConnections" = "5"
}
```

Only with a persistent store does `@PersistJobDataAfterExecution` mean anything: with the
default `RAMJobStore` the `JobDataMap` lives in memory and dies with the process.

---

## Clustering

Clustering is Quartz's own feature, configured through the same passthrough:

```hocon
scheduling.quartz.properties {
  "org.quartz.jobStore.class"       = "org.quartz.impl.jdbcjobstore.JobStoreTX"
  "org.quartz.jobStore.isClustered" = "true"
  "org.quartz.jobStore.clusterCheckinInterval" = "20000"
  # instanceName must be identical on every node; instanceId must differ
  "org.quartz.scheduler.instanceName" = "my-service-scheduler"
  "org.quartz.scheduler.instanceId"   = "AUTO"
}
```

Kora already defaults `instanceId` to `AUTO` and `instanceName` to `kora-quartz-scheduler`, so a
cluster works on the defaults as long as every node runs the same configuration. Clustering
requires a JDBC job store — `RAMJobStore` cannot cluster.

Constraints that follow from how Kora registers jobs (`KoraQuartzJobRegistrar` /
`KoraQuartzJobFactory`):

- **Every node must run the same build.** Job instances are resolved from the DI graph by the
  generated job class; unknown classes fall through to Quartz's `PropertySettingJobFactory`,
  which needs a no-arg constructor that generated job classes do not have. A node cannot execute
  a job class it does not itself carry.
- **The registrar is the source of truth for triggers.** At every `init()` and `graphRefreshed()`
  it re-adds each job durably and reconciles its triggers against the compiled/config state,
  unscheduling any others. Trigger rows edited directly in `QRTZ_TRIGGERS` are reverted.
- Jobs are keyed by the generated job class's canonical name, so renaming a job class or its
  method orphans the old rows in the job store; clean them up yourself.

---

## Keys removed in 2.0

| Kora 1.x | Kora 2.0 | Failure mode |
|---|---|---|
| `quartz { "org.quartz.*" = … }` (root) | `scheduling.quartz.properties { … }` | silent — Quartz starts on its own defaults |
| `scheduling.waitForJobComplete` | `scheduling.quartz.waitForJobComplete` (default now `true`) | silent — shutdown behaviour reverts to the default |
| `ru.tinkoff.kora:scheduling-quartz` | `io.koraframework:scheduling-quartz` | dependency resolution failure |
| `ru.tinkoff.kora:kora-parent` | `io.koraframework:kora-bom` | dependency resolution failure |

The 1.x claim that `scheduling.telemetry.metrics.enabled` defaults to `true` no longer holds:
in 2.0 both metrics and logging default to `false`.

---

## See also

- [quartz-scheduling-reference.md](quartz-scheduling-reference.md) — annotations, generated code, cron grammar
- [graceful-shutdown-reference.md](graceful-shutdown-reference.md) — shutdown semantics
- [kora-config-hocon](../../kora-config-hocon/SKILL.md) — HOCON substitution and typed config
- [kora-telemetry-metrics](../../kora-telemetry-metrics/SKILL.md) — wiring a `MeterRegistry`
- [Quartz configuration reference](https://www.quartz-scheduler.org/documentation/quartz-2.3.0/configuration/) — upstream property list
