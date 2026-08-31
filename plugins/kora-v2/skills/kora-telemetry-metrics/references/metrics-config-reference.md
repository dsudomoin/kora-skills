# Metrics Configuration Reference (Kora 2.0)

Everything that decides whether a metric is recorded, and where its config lives.

## Contents

- [The two gates](#the-two-gates)
- [Module setup](#module-setup)
- [The complete `telemetry.metrics` key set](#key-set)
- [Per-component config roots](#config-roots)
- [`slo` — histogram buckets](#slo)
- [`tags` — extra static tags](#tags)
- [`driverMetrics` — the third-party pool/client meters](#driver-metrics)
- [The system server](#system-server)
- [Common tags via `PrometheusMeterRegistryInitializer`](#common-tags)
- [Replacing the registry or the scraper](#replacing-the-registry)
- [Keys that no longer exist](#removed-keys)
- [Verification](#verification)

---

## The two gates { #the-two-gates }

A Kora component records metrics only when **both** conditions hold.

### Gate 1 — `metrics.enabled`, which defaults to `false`

`io.koraframework.telemetry.common.TelemetryConfig`:

```java
@ConfigMapper
interface LoggingConfig { default boolean enabled() { return false; } }

@ConfigMapper
interface TracingConfig { default boolean enabled() { return true; } … }

@ConfigMapper
interface MetricsConfig {
    Duration[] DEFAULT_SLO = { /* 1, 10, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 30000, 60000, 90000 ms */ };
    default boolean enabled() { return false; }
    default Duration[] slo() { return DEFAULT_SLO; }
    default Map<String, String> tags() { return Map.of(); }
}
```

Metrics **and** logging are off; tracing is on. There is one documented exception to the tracing
default: `SystemHttpServerConfig.SystemHttpServerTelemetryConfig.SystemHttpServerTracingConfig`
overrides `enabled()` back to `false`, so the system server is not traced unless you say so.

### Gate 2 — a `MeterRegistry` must exist in the graph

Every `Default*TelemetryFactory` takes `@Nullable MeterRegistry` and computes:

```java
var metricEnabled = this.meterRegistry != null && config.metrics().enabled();
```

`HttpServerModule.defaultHttpServerTelemetryFactory(@Nullable MeterRegistry meterRegistry, …)` is
how it reaches the factory, and the only component that supplies a `MeterRegistry` is
`MetricsModule`. Without the module the parameter is `null` and the config flag is inert — no error,
no warning.

When either gate fails the factory substitutes `NoopMeterRegistry.INSTANCE`
(`io.koraframework.micrometer.common.NoopMeterRegistry`) and a `Noop*MetricsFactory`, so every
recording call still executes and silently discards its value. That is why nothing ever fails
loudly.

### Truth table

| `MetricsModule` on `@KoraApp` | `telemetry.metrics.enabled` | Component metrics | `/metrics` body |
|---|---|---|---|
| no | not set (`false`) | none | `# Metric Scraper disabled` |
| no | `true` | **none** | `# Metric Scraper disabled` |
| yes | not set (`false`) | **none** | `kora_up` + JVM meters only |
| yes | `true` | recorded | `kora_up` + JVM + component meters |

---

## Module setup { #module-setup }

```java
package com.example;

import io.koraframework.application.graph.KoraApplication;
import io.koraframework.common.annotation.KoraApp;
import io.koraframework.config.hocon.HoconConfigModule;
import io.koraframework.http.server.undertow.UndertowPublicHttpServerModule;
import io.koraframework.logging.logback.LogbackModule;
import io.koraframework.micrometer.module.MetricsModule;

@KoraApp
public interface Application extends
        HoconConfigModule,
        LogbackModule,
        MetricsModule,
        UndertowPublicHttpServerModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

`MetricsModule` contributes three components, all replaceable:

| Method | Provides | Notes |
|---|---|---|
| `prometheusMeterRegistry(All<PrometheusMeterRegistryInitializer>)` | `@Root @DefaultComponent Wrapped<MeterRegistry>` | a `PrometheusMeterRegistryWrapper`; `@Root` so it starts even if nothing injects it |
| `prometheusMetricsScraper(MeterRegistry)` | `@DefaultComponent MetricsScraper` | `prometheus::scrape` for a `PrometheusMeterRegistry`, otherwise a no-op writer |
| `micrometerMeterProvider(MeterRegistry, @Nullable CallbackRegistrar)` | `@DefaultComponent MicrometerMeterProvider` | the OpenTelemetry ↔ Micrometer bridge |

An HTTP server module is required for the scrape endpoint; `UndertowPublicHttpServerModule`
already extends `UndertowSystemHttpServerModule`, which registers it.

---

## The complete `telemetry.metrics` key set { #key-set }

`MetricsConfig` declares exactly three settings. Every component's `*MetricsConfig` extends it, and
only two add anything.

| Key | Type | Default | Applies to |
|---|---|---|---|
| `enabled` | boolean | `false` | every component |
| `slo` | duration array | `MetricsConfig.DEFAULT_SLO` (14 buckets, 1 ms … 90 s) | every component that records a Timer |
| `tags` | map<string,string> | `{}` | every component |
| `driverMetrics` | boolean | **`true`** | `jdbc`, `cassandra` (`DatabaseMetricsConfig`) |
| `driverMetrics` | boolean | **`false`** | Kafka consumer and publisher |
| `engineMetrics` | boolean | `false` | `camunda.engine.bpmn` (experimental) |

There is **no** `histogram`, `percentiles`, `prefix`, `disabled`, `opentelemetrySpec` or
`registry` key. If a config key is not in this table, it does not exist.

Full shape:

```hocon
httpServer.telemetry.metrics {
  enabled = true
  slo = [ 1, 10, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 30000, 60000, 90000 ]
  tags {
    "deployment.environment" = "production"
    "service.instance.id" = ${?HOSTNAME}
  }
}
```

---

## Per-component config roots { #config-roots }

`telemetry.metrics` hangs off each component's own config root. Verified from the module that
resolves each path:

| Component | Config root | Resolved in |
|---|---|---|
| HTTP server (public) | `httpServer` | `UndertowPublicHttpServerModule` → `UndertowHttpServerFactoryModule("kora-undertow", "httpServer")` |
| HTTP server (system) | `httpServer.system` | `UndertowSystemHttpServerModule` / `SystemHttpServerModule` |
| HTTP client | `httpClient.<name>` | `@HttpClient("name")`; OkHttp/JDK/Apache modules all use the `httpClient` prefix |
| JDBC | `jdbc` | `JdbcDatabaseModule` → `JdbcDatabaseFactoryModule("jdbc")` |
| Cassandra | `cassandra` | `CassandraDatabaseModule` → `CassandraDatabaseFactoryModule("cassandra")` |
| gRPC server | `grpcServer` | `GrpcServerModule` → `GrpcServerFactoryModule("kora-grpc", "grpcServer")` |
| gRPC client | `grpcClient.<ServiceSimpleName>` | `GrpcClientConfig` → `config.get("grpcClient." + serviceSimpleName)` |
| Kafka consumer | the `@KafkaListener("path")` value | annotation javadoc: *"@return config path"* |
| Kafka publisher | the `@KafkaPublisher("path")` value | annotation javadoc: *"@return path to config"* |
| Cache (Caffeine / Redis) | the `@Cache("path")` value | annotation javadoc: *"path for cache config (cache name)"* |
| Scheduling | `scheduling.telemetry` | `SchedulingModule` → `config.get("scheduling.telemetry")` |
| Resilience (global) | `resilient.telemetry` | `ResilientModule` → `config.get("resilient.telemetry")` |
| Resilience (per operation) | `resilient.<kind>.<name>.telemetry` | merged over the global section, operation values win |
| Redis / Lettuce | `lettuce` | `LettuceModule` → `LettuceFactoryModule("lettuce")` |
| S3 (AWS SDK) | `s3client.aws` | `AwsS3ClientModule` |
| JMS | the listener's config path | `JmsConsumerTelemetryConfig` |

A realistic "turn everything on" block:

```hocon
httpServer {
  port = 8080
  system.port = 8085
  telemetry.metrics.enabled = true
}

httpClient.petApi {
  url = "http://pets:8080"
  telemetry.metrics.enabled = true
}

jdbc {
  jdbcUrl = ${POSTGRES_JDBC_URL}
  username = ${POSTGRES_USER}
  password = ${POSTGRES_PASS}
  poolName = "orders"
  telemetry.metrics.enabled = true
}

scheduling.telemetry.metrics.enabled = true
resilient.telemetry.metrics.enabled = true
```

> Named resilience sections **do not inherit** from a `default` section in 2.0 — each stands alone
> and unset fields fall back to the type's defaults. See [kora-aop-resilient](../../kora-aop-resilient/SKILL.md).

---

## `slo` — histogram buckets { #slo }

`slo()` returns `Duration[]` and is passed straight to Micrometer:

```java
return Timer.builder("http.server.request.duration")
    .serviceLevelObjectives(this.context.config().metrics().slo())
    .tags(Tags.of(staticTags));
```

`DurationConfigValueMapper` decides how each element is read:

| HOCON element | Parsed as |
|---|---|
| `100` (bare number) | **100 milliseconds** — `Duration.ofMillis(number.longValue())` |
| `"250ms"`, `"1s"`, `"2m"`, `"1h"`, `"3d"` | HOCON-style duration (`ns`, `us`, `ms`, `s`, `m`, `h`, `d`) |
| `"PT1.5S"` | ISO-8601, tried first via `Duration.parse` |
| `null` inside the array | `ConfigValueException` — the array mapper rejects null elements |

So a 1.x millisecond array carries over unchanged. Buckets are cumulative: each `_bucket{le="…"}`
series counts observations at or below that boundary, which is what
`histogram_quantile` needs.

```hocon
# a tight web-facing SLO set instead of the 14 defaults
httpServer.telemetry.metrics {
  enabled = true                                   # slo does nothing without this
  slo = [ 5, 25, 50, 100, 250, 500, 1000, 5000 ]
}
```

Fewer buckets means fewer time series. The default 14 buckets multiply every distinct tag
combination by 14 `_bucket` series plus `_count`/`_sum`/`_max` — trim them for high-cardinality
routes.

For **your own** meters, set buckets on the builder — the config key governs Kora's meters only:

```java
Timer.builder("api.request.duration")
    .serviceLevelObjectives(Duration.ofMillis(50), Duration.ofMillis(100), Duration.ofSeconds(1))
    .register(registry);
```

`publishPercentiles(...)` computes percentiles inside the process and costs CPU and memory; with
Prometheus prefer SLO buckets and `histogram_quantile` server-side.

---

## `tags` — extra static tags { #tags }

Every factory appends `config.metrics().tags()` to the tag list of every meter it creates. The
tags are **static per component** — they are read when the meter is registered, so a changing value
would create a new series.

```hocon
jdbc.telemetry.metrics {
  enabled = true                 # tags do nothing without this
  tags {
    "db.role" = "primary"
    "team" = "orders"
  }
}
```

Use this for per-component labels. For labels that belong on *every* series in the process, use
[`PrometheusMeterRegistryInitializer`](#common-tags) instead — a common tag set applied in fifteen
config blocks is fifteen chances to drift.

---

## `driverMetrics` — third-party pool and client meters { #driver-metrics }

`DatabaseTelemetryConfig.DatabaseMetricsConfig` adds one key on top of the standard three:

```java
interface DatabaseMetricsConfig extends MetricsConfig {
    default boolean driverMetrics() { return true; }
}
```

`JdbcDataSource`:

```java
this.dataSource = new HikariDataSource(JdbcDatabaseConfig.toHikariConfig(this.databaseConfig, configurer));
if (this.databaseConfig.telemetry().metrics().driverMetrics()) {
    this.dataSource.setMetricRegistry(this.telemetry.meterRegistry());
}
```

**The trap:** `driverMetrics` defaults to `true`, but `telemetry.meterRegistry()` is the registry
the *telemetry factory* produced. With `jdbc.telemetry.metrics.enabled = false` the factory returned
`NoopDatabaseTelemetry`, whose `meterRegistry()` is `DefaultDatabaseTelemetryFactory.NOOP_METER_REGISTRY`.
Hikari dutifully registers its meters into a registry that throws them away. So:

```hocon
jdbc.telemetry.metrics {
  enabled = true        # REQUIRED, even though driverMetrics is already true
  driverMetrics = true  # default; set false to skip Hikari's own meters
}
```

Kafka's `driverMetrics` defaults to **`false`** on both the consumer and the publisher — turn it on
explicitly if you want the Kafka client's own meters.

---

## The system server { #system-server }

`SystemHttpServerConfig extends HttpServerConfig`:

```java
@Override default int port()  { return 8085; }
default String metricsPath()   { return "/metrics"; }
default String readinessPath() { return "/system/readiness"; }
default String livenessPath()  { return "/system/liveness"; }
```

```hocon
httpServer.system {
  port = 8085
  metricsPath = "/metrics"
  readinessPath = "/system/readiness"
  livenessPath = "/system/liveness"

  # optional: instrument the system server itself
  telemetry.metrics.enabled = true
  telemetry.tracing.enabled = true   # its tracing default is false, unlike every other component
}
```

A stale 1.x key (`privateApiHttpPort`, `privateApiHttpMetricsPath`, …) is an unrecognised HOCON key.
It is ignored with no warning, both servers fall back to their own defaults (8080 / 8085), and a
service that deliberately used custom ports comes up green on the wrong ones.

---

## Common tags via `PrometheusMeterRegistryInitializer` { #common-tags }

`MetricsModule` collects them with `All<PrometheusMeterRegistryInitializer>` and applies them in
`PrometheusMeterRegistryWrapper.init()` **before** binding the JVM binders and registering
`kora.up` — so common tags land on those meters too:

```java
var meterRegistry = new PrometheusMeterRegistry(PrometheusConfig.DEFAULT);
for (var initializer : initializers) {
    meterRegistry = initializer.apply(meterRegistry);
}
this.gcMetrics = new JvmGcMetrics();
new ClassLoaderMetrics().bindTo(meterRegistry);
…
```

The type is `Function<PrometheusMeterRegistry, PrometheusMeterRegistry>`, so it **must return the
registry** — returning `void`, or returning a fresh registry you did not derive from the argument,
breaks the chain.

Java:

```java
package com.example;

import io.koraframework.common.annotation.Module;
import io.koraframework.micrometer.module.PrometheusMeterRegistryInitializer;

@Module
public interface CustomMetricsConfig {

    default PrometheusMeterRegistryInitializer commonTagsInit() {
        return registry -> {
            registry.config().commonTags(
                    "service", "order-service",
                    "environment", "production",
                    "version", "1.0.0");
            return registry;
        };
    }
}
```

Kotlin:

```kotlin
package com.example

import io.koraframework.common.annotation.Module
import io.koraframework.micrometer.module.PrometheusMeterRegistryInitializer

@Module
interface CustomMetricsConfig {

    fun commonTagsInit(): PrometheusMeterRegistryInitializer =
        PrometheusMeterRegistryInitializer { registry ->
            registry.config().commonTags(
                "service", "order-service",
                "environment", "production",
                "version", "1.0.0")
            registry
        }
}
```

Multiple initializers are allowed and are applied in graph order. This is also the only hook Kora
gives you onto `registry.config()`, so anything Micrometer exposes there — `commonTags(...)`,
`meterFilter(io.micrometer.core.instrument.config.MeterFilter)`, `namingConvention(...)` — is
reachable from here. Cardinality caps and rename/deny rules are `MeterFilter`s; consult the
[Micrometer meter-filter documentation](https://docs.micrometer.io/micrometer/reference/concepts/meter-filters.html)
for the factory you need, since those APIs belong to Micrometer, not Kora.

Useful common tags: `service`, `environment`, `version`, `region`. Keep them bounded — they multiply
onto every series in the process.

---

## Replacing the registry or the scraper { #replacing-the-registry }

Both `MetricsModule` factory methods are `@DefaultComponent`, so a `@Component` of the same type in
your graph wins. Two consequences worth knowing:

- Supplying a non-Prometheus `MeterRegistry` leaves `prometheusMetricsScraper` matching the
  `else` branch, which returns `os -> {}`. `/metrics` then answers **200 with an empty body**.
  Supply your own `MetricsScraper` alongside the registry.
- `MetricsScraper` is a single-method contract:

  ```java
  public interface MetricsScraper {
      void scrape(OutputStream os) throws IOException;
  }
  ```

  `MetricsHandler` resolves it as `ValueOf<Optional<MetricsScraper>>`, so an absent scraper is not a
  graph error — it is the `# Metric Scraper disabled` response.

---

## Keys that no longer exist { #removed-keys }

| 1.x key | Status in 2.0 |
|---|---|
| `metrics.opentelemetrySpec` (`V120` / `V123`) | **removed** — no source reads it, and no module resolves a top-level `metrics` config path |
| `httpServer.publicApiHttpPort` | → `httpServer.port` |
| `httpServer.privateApiHttpPort` | → `httpServer.system.port` |
| `httpServer.privateApiHttpMetricsPath` | → `httpServer.system.metricsPath` |
| `httpServer.privateApiHttpLivenessPath` / `…ReadinessPath` | → `httpServer.system.livenessPath` / `…readinessPath` |
| `db { … }` | → `jdbc { … }` |

2.0 emits a single fixed naming scheme — the one 1.x called `V123` — with several meter names
changed on top of it. Setting `opentelemetrySpec` in a 2.0 config silently does nothing.

---

## Verification { #verification }

```bash
# 1. the registry is bound at all
curl -s http://localhost:8085/metrics | head -1
#    "# Metric Scraper disabled"  -> MetricsModule missing

# 2. component instrumentation is actually on
curl -s http://localhost:8080/your/route > /dev/null
curl -s http://localhost:8085/metrics | grep -c '^http_server_request_duration'
#    0 -> httpServer.telemetry.metrics.enabled is still false

# 3. common tags landed
curl -s http://localhost:8085/metrics | grep '^kora_up'
```

In a JUnit test, assert on the body rather than the status code — a 200 from `/metrics` proves only
that the system server is up:

```java
assertTrue(body.contains("http_server_request_duration") || body.contains("http_server_active_requests"));
assertFalse(body.contains("# Metric Scraper disabled"));
```

---

## References

- [metrics-reference.md](metrics-reference.md) — what each enabled component actually emits
- [metrics-export-reference.md](metrics-export-reference.md) — scraping and forwarding
- [probes-reference.md](probes-reference.md) — the other endpoints on the same system server
- [Micrometer concepts](https://docs.micrometer.io/micrometer/reference/concepts.html)
