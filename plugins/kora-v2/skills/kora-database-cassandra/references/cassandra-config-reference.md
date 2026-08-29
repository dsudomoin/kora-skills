# Cassandra Configuration Reference

**Artifact:** `io.koraframework:database-cassandra`
**Config interface:** `io.koraframework.database.cassandra.CassandraConfig`
**Config section:** `cassandra`
**Driver:** DataStax Java Driver 4.19.3 (`org.apache.cassandra:java-driver-core`)

## Contents

- [Section name](#section-name)
- [Minimal configuration](#minimal-configuration)
- [cassandra.auth](#cassandraauth)
- [cassandra.basic](#cassandrabasic)
- [cassandra.advanced](#cassandraadvanced)
- [cassandra.profiles](#cassandraprofiles)
- [cassandra.telemetry](#cassandratelemetry)
- [Code-level configuration](#code-level-configuration)
- [Multiple sessions](#multiple-sessions)
- [Keys that do not exist](#keys-that-do-not-exist)
- [Startup failures](#startup-failures)

---

## Section name

`CassandraDatabaseModule` wires the session at the fixed path `cassandra`:

```java
public interface CassandraDatabaseModule extends CassandraMapperModule {

    @FactoryModule
    default CassandraDatabaseFactoryModule cassandraDatabase() {
        return new CassandraDatabaseFactoryModule("cassandra");
    }
}
```

The section is still called `cassandra` in Kora 2.0 — JDBC's `db` → `jdbc` rename has no Cassandra
counterpart, and the `basic` / `advanced` / `profiles` / `auth` / `telemetry` layout is unchanged.
What did change is the telemetry **defaults**: logging and metrics are now off unless enabled.

To bind a session to a different path, declare your own `CassandraDatabaseFactoryModule`
([Multiple sessions](#multiple-sessions)).

---

## Minimal configuration

```hocon
cassandra {
  auth {
    login = ${CASSANDRA_USER}
    password = ${CASSANDRA_PASS}
  }
  basic {
    contactPoints = ${CASSANDRA_CONTACT_POINTS}
    dc = ${CASSANDRA_DC}
    sessionKeyspace = ${CASSANDRA_KEYSPACE}
    request {
      timeout = 5s
    }
  }
  telemetry.logging.enabled = true
}
```

```yaml
cassandra:
  auth:
    login: ${CASSANDRA_USER}
    password: ${CASSANDRA_PASS}
  basic:
    contactPoints: ${CASSANDRA_CONTACT_POINTS}
    dc: ${CASSANDRA_DC}
    sessionKeyspace: ${CASSANDRA_KEYSPACE}
    request:
      timeout: 5s
  telemetry:
    logging:
      enabled: true
```

`contactPoints` is the only structurally required value: it is a non-nullable `List<String>` of
`host:port` entries, and everything else is `@Nullable` or defaulted. `auth` as a whole is
`@Nullable`, but when present both `login` and `password` are required.

---

## cassandra.auth

| Key | Type | Notes |
|---|---|---|
| `auth.login` | `String` | required when `auth` is present |
| `auth.password` | `String` | required when `auth` is present |

Applied as `CqlSessionBuilder#withAuthCredentials(login, password)`. Omit the whole `auth` block
for a cluster without authentication.

---

## cassandra.basic

| Key | Type | Notes |
|---|---|---|
| `basic.contactPoints` | `List<String>` | **required**, `host:port` |
| `basic.dc` | `String?` | local datacenter → `withLocalDatacenter` |
| `basic.sessionKeyspace` | `String?` | keyspace bound to the session |
| `basic.sessionName` | `String?` | driver session name in logs/metrics; also the telemetry name, default `cassandra` |
| `basic.request.timeout` | `Duration?` | per-request timeout |
| `basic.request.consistency` | `String?` | e.g. `LOCAL_QUORUM` |
| `basic.request.serialConsistency` | `String?` | `SERIAL` or `LOCAL_SERIAL`, for LWT |
| `basic.request.pageSize` | `Integer?` | rows per round trip |
| `basic.request.defaultIdempotence` | `Boolean?` | gates retries and speculative execution |
| `basic.loadBalancingPolicy.slowReplicaAvoidance` | `Boolean?` | driver slow-replica avoidance |
| `basic.cloud.secureConnectBundle` | `String?` | path/URL of an Astra secure connect bundle |

See [Consistency Reference](consistency-reference.md) for the request keys in depth.

---

## cassandra.advanced

Everything under `advanced` is optional and maps 1:1 onto DataStax `DefaultDriverOption`s in
`CassandraSessionBuilderUtils`. The full set of subsections:

| Subsection | Keys |
|---|---|
| `advanced.sessionLeak` | `threshold` |
| `advanced.connection` | `connectTimeout`, `initQueryTimeout`, `setKeyspaceTimeout`, `maxRequestsPerConnection`, `maxOrphanRequests`, `warnOnInitError`, `pool.localSize`, `pool.remoteSize` |
| `advanced.reconnectOnInit` | boolean |
| `advanced.reconnectionPolicy` | `baseDelay`, `maxDelay` |
| `advanced.loadBalancingPolicy.dcFailover` | `maxNodesPerRemoveDc`, `allowForLocalConsistencyLevels` |
| `advanced.sslEngineFactory` | `cipherSuites`, `hostnameValidation`, `keystorePath`, `keystorePassword`, `truststorePath`, `truststorePassword` |
| `advanced.timestampGenerator` | `forceJavaClock`, `driftWarning.threshold`, `driftWarning.interval` |
| `advanced.protocol` | `version`, `compression`, `maxFrameLength` |
| `advanced.request` | `warnIfSetKeyspace`, `logWarnings`, `trace.attempts`, `trace.interval`, `trace.consistency` |
| `advanced.metrics` | `idGenerator.name` (default `TaggingMetricIdGenerator`), `idGenerator.prefix`, `publishPercentileHistogram`, `node.enabled`, `node.cqlMessages.*`, `session.enabled`, `session.cqlRequests.*`, `session.throttlingDelay.*` |
| `advanced.socket` | `tcpNoDelay`, `keepAlive`, `reuseAddress`, `lingerInterval`, `receiveBufferSize`, `sendBufferSize` |
| `advanced.heartbeat` | `interval`, `timeout` |
| `advanced.metadata` | `schema.enabled`, `schema.requestTimeout`, `schema.requestPageSize`, `schema.refreshedKeyspaces`, `schema.debouncer.window`, `schema.debouncer.maxEvents`, `topologyEventDebouncer.window`, `topologyEventDebouncer.maxEvents`, `tokenMapEnabled` |
| `advanced.controlConnection` | `timeout`, `schemaAgreement.interval`, `schemaAgreement.timeout`, `schemaAgreement.warnOnFailure` |
| `advanced.preparedStatements` | `prepareOnAllNodes`, `reprepareOnUp.{enabled, checkSystemTable, maxStatements, maxParallelism, timeout}`, `preparedCache.weakValues` |
| `advanced.netty` | `ioGroup.size`, `ioGroup.shutdown.{quietPeriod, timeout, unit}`, `adminGroup.size`, `adminGroup.shutdown.*`, `timer.tickDuration`, `timer.ticksPerWheel`, `daemon` |
| `advanced.coalescer` | `rescheduleInterval` |
| `advanced.resolveContactPoints` | boolean |
| `advanced.throttler` | `throttlerClass`, `maxConcurrentRequests`, `maxRequestsPerSecond`, `maxQueueSize`, `drainInterval` |

A typical production block:

```hocon
cassandra.advanced {
  connection {
    connectTimeout = 10s
    initQueryTimeout = 10s
    pool {
      localSize = 4
      remoteSize = 1
    }
  }
  reconnectOnInit = true
  reconnectionPolicy {
    baseDelay = 1s
    maxDelay = 60s
  }
  request.logWarnings = true
}
```

The driver-metrics subsections (`advanced.metrics.node.*`, `advanced.metrics.session.*`) carry
their own defaults: node metrics default to open connections, in-flight, bytes sent/received,
write/read timeouts and aborted requests; session metrics to connected nodes, CQL requests, client
timeouts, prepared-cache size and throttling delay/queue size. Histogram tuning per metric is
`lowestLatency` (1 ms), `highestLatency` (90 s), `significantDigits`, `refreshInterval` and `slo`.

---

## cassandra.profiles

`cassandra.profiles.<name>` declares a driver execution profile selected per method with
`@CassandraProfile("<name>")`. A profile accepts the overridable subset of `basic` and `advanced`;
`contactPoints` is fixed at the root. Full treatment in the
[Consistency Reference](consistency-reference.md#driver-profiles).

```hocon
cassandra.profiles.analytics {
  basic.request.consistency = "ONE"
  basic.request.timeout = 30s
  basic.request.pageSize = 1000
}
```

---

## cassandra.telemetry

`cassandra.telemetry` is a `DatabaseTelemetryConfig`, i.e. the framework `TelemetryConfig` plus one
Cassandra-relevant addition:

| Key | Default | Notes |
|---|---|---|
| `telemetry.logging.enabled` | **`false`** | query logging |
| `telemetry.metrics.enabled` | **`false`** | Kora `db_*` metrics |
| `telemetry.metrics.slo` | 1, 10, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 30000, 60000, 90000 ms | histogram buckets |
| `telemetry.metrics.tags` | `{}` | extra metric tags |
| `telemetry.metrics.driverMetrics` | **`true`** | registers the driver's own Micrometer metrics factory |
| `telemetry.tracing.enabled` | `true` | |
| `telemetry.tracing.attributes` | `{}` | extra span attributes |

```hocon
cassandra.telemetry {
  logging.enabled = true
  metrics {
    enabled = true
    driverMetrics = true
  }
  tracing.enabled = true
}
```

Two things bite here:

1. **Logging and metrics are off by default.** An example that claims to demonstrate query logs or
   `db_*` metrics must enable them explicitly; otherwise the app runs green and produces nothing.
2. `driverMetrics` defaults to `true`, and when it is on Kora sets the driver's
   `METRICS_FACTORY_CLASS` to `MicrometerMetricsFactory` and passes the meter registry to
   `CqlSessionBuilder#withMetricRegistry`. That publishes the DataStax driver's own metrics
   (`advanced.metrics.*`) alongside Kora's `db_*` ones.

The telemetry name is `basic.sessionName` when set, otherwise `cassandra`.

---

## Code-level configuration

There is no `CassandraConfigurer` in Kora 2.0. The session factory takes two optional
`io.koraframework.common.Configurer<T>` components:

```java
public class CassandraDatabaseFactoryModule {

    @Tag(Tag.Factory.class)
    public CassandraSession cassandraSession(
        @Tag(Tag.Factory.class) CassandraConfig config,
        DatabaseTelemetryFactory telemetryFactory,
        @Tag(Tag.Factory.class) @Nullable Configurer<ProgrammaticDriverConfigLoaderBuilder> loaderConfigurer,
        @Tag(Tag.Factory.class) @Nullable Configurer<CqlSessionBuilder> sessionBuilderConfigurer) { … }
}
```

`Configurer<T>` is a single-method interface, `T configure(T t)`. Both parameters are `@Nullable`,
so supplying neither is fine.

```java
@Component
public final class MyCassandraSessionConfigurer implements Configurer<CqlSessionBuilder> {

    @Override
    public CqlSessionBuilder configure(CqlSessionBuilder builder) {
        return builder.withClientId(UUID.randomUUID());
    }
}

@Component
public final class MyCassandraDriverConfigurer implements Configurer<ProgrammaticDriverConfigLoaderBuilder> {

    @Override
    public ProgrammaticDriverConfigLoaderBuilder configure(ProgrammaticDriverConfigLoaderBuilder builder) {
        return builder.withBoolean(DefaultDriverOption.METADATA_TOKEN_MAP_ENABLED, false);
    }
}
```

`@Tag(Tag.Factory.class)` on those parameters means "the tag of the enclosing factory-module
method". `CassandraDatabaseModule#cassandraDatabase()` has no tag, so a plain untagged
`@Component` is picked up. If you declare a **tagged** factory module for a second session, its
configurers must carry the same tag.

The loader configurer runs against the fully populated `ProgrammaticDriverConfigLoaderBuilder`, so
it can reach any `DefaultDriverOption` Kora does not expose through `CassandraConfig`. It runs
**after** Kora has written every key from `CassandraConfig`, so it also overrides them.

The session itself is a `Wrapped<CqlSession>` and a `Lifecycle`, so
`com.datastax.oss.driver.api.core.CqlSession` can also be injected directly when you need raw
driver access outside a repository.

---

## Multiple sessions

```java
public interface AnalyticsCassandraModule extends CassandraMapperModule {

    @Tag(Analytics.class)
    @FactoryModule
    default CassandraDatabaseFactoryModule analyticsCassandra() {
        return new CassandraDatabaseFactoryModule("cassandraAnalytics");
    }
}
```

```hocon
cassandra { basic.contactPoints = ["primary:9042"], … }
cassandraAnalytics { basic.contactPoints = ["analytics:9042"], … }
```

```java
@Repository(executorTag = Analytics.class)
public interface AnalyticsRepository extends CassandraRepository { … }
```

Extend `CassandraMapperModule` rather than `CassandraDatabaseModule` in the extra module, so the
untagged default session is not declared twice.

---

## Keys that do not exist

These appear in Kora 1.x-era notes, other frameworks, or plain invention. None is read by
`CassandraConfig`, and an unrecognised HOCON key is ignored **without a warning** — the setting
silently stays at its default:

`cassandra.healthCheck`, `cassandra.additionalKeyspaces`, `cassandra.retryPolicy`,
`cassandra.basic.consistency`, `cassandra.basic.connection`, `cassandra.requestTimeout`,
`cassandra.readConsistency`, `cassandra.writeConsistency`, `cassandra.username` /
`cassandra.password` at the root (they live under `auth.login` / `auth.password`),
`cassandra.keyspace` (it is `basic.sessionKeyspace`), `cassandra.loadBalancing.policy`.
`@Repository` has no `keyspace` attribute either — only `executorTag`.

---

## Startup failures

`CassandraSession#init` wraps any driver failure:

```
IllegalStateException: CassandraSession failed to start for contact points [host:9042],
keyspace 'ks', datacenter 'dc1': <driver message>; check contact points, local datacenter,
keyspace, credentials, TLS, and network access
```

| Nested cause | Fix |
|---|---|
| `ConnectTimeoutException` / `AllNodesFailedException` | contact points, port, network policy, TLS |
| `InvalidQueryException: Keyspace 'x' does not exist` | create the keyspace before startup (migrations run separately for Cassandra) |
| `IllegalStateException` about the local datacenter | `basic.dc` must match a real DC name in the ring |
| `AuthenticationException` | `auth.login` / `auth.password`, and that the cluster requires auth at all |
| `ConfigValueException: Config expected value, but got null at path: 'ROOT.cassandra.basic.contactPoints'` | `contactPoints` is required |

Set `advanced.reconnectOnInit = true` to let the driver keep retrying at startup instead of failing
the graph when no contact point answers.

---

## See Also

- [Consistency Reference](consistency-reference.md)
- [CQL Repository Reference](cql-repository-reference.md)
