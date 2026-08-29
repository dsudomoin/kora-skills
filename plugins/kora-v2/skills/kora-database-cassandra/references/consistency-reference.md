# Consistency and Driver Profiles Reference

**Artifact:** `io.koraframework:database-cassandra`
**Config interface:** `io.koraframework.database.cassandra.CassandraConfig`
**Annotation:** `io.koraframework.database.cassandra.annotation.CassandraProfile`

## Contents

- [Where request tuning lives](#where-request-tuning-lives)
- [Consistency levels](#consistency-levels)
- [Driver profiles](#driver-profiles)
- [@CassandraProfile](#cassandraprofile)
- [Per-query overrides without a profile](#per-query-overrides-without-a-profile)
- [Serial consistency and LWT](#serial-consistency-and-lwt)
- [Multi-DC](#multi-dc)
- [Tuning rules](#tuning-rules)
- [Troubleshooting](#troubleshooting)

---

## Where request tuning lives

Everything request-level sits under `cassandra.basic.request`
(`CassandraConfig.Basic.BasicRequestConfig`), and each key maps onto a DataStax
`DefaultDriverOption` in `CassandraSessionBuilderUtils#applyOverridable`:

| Config key | Driver option | Type |
|---|---|---|
| `basic.request.timeout` | `REQUEST_TIMEOUT` | `Duration` |
| `basic.request.consistency` | `REQUEST_CONSISTENCY` | `String` |
| `basic.request.pageSize` | `REQUEST_PAGE_SIZE` | `Integer` |
| `basic.request.serialConsistency` | `REQUEST_SERIAL_CONSISTENCY` | `String` |
| `basic.request.defaultIdempotence` | `REQUEST_DEFAULT_IDEMPOTENCE` | `Boolean` |
| `basic.loadBalancingPolicy.slowReplicaAvoidance` | `LOAD_BALANCING_POLICY_SLOW_AVOIDANCE` | `Boolean` |

All five are `@Nullable` — an unset key is simply not written to the driver config, so the driver's
own default applies.

There are **no** `readConsistency`, `writeConsistency`, `requestTimeout`, `retryPolicy` or
`basic.consistency` keys. A key that does not exist is an unrecognised HOCON entry: it is ignored
without a warning and the setting silently stays at the driver default.

---

## Consistency levels

Values are the driver's `ConsistencyLevel` names, passed through as strings.

| Level | Meaning | Typical use |
|---|---|---|
| `ANY` | write acknowledged by a hint | fire-and-forget writes only |
| `ONE` / `TWO` / `THREE` | N replicas anywhere | low-latency reads, caches |
| `QUORUM` | majority across all DCs | single-DC general purpose |
| `ALL` | every replica | rarely — one node down blocks the query |
| `LOCAL_ONE` | one replica in the local DC | multi-DC low-latency reads |
| `LOCAL_QUORUM` | majority in the local DC | multi-DC default |
| `EACH_QUORUM` | quorum in every DC (writes) | cross-DC strong writes |
| `SERIAL` / `LOCAL_SERIAL` | Paxos read/serial level | only for LWT (`serialConsistency`) |

`SERIAL` and `LOCAL_SERIAL` belong in `serialConsistency`, not in `consistency`.

---

## Driver profiles

`cassandra.profiles.<name>` declares a named driver execution profile. `setProfile` opens a driver
profile and applies exactly the same overridable keys, so a profile is written as a partial copy of
the root shape:

```hocon
cassandra {
  basic {
    contactPoints = ${CASSANDRA_CONTACT_POINTS}
    dc = ${CASSANDRA_DC}
    sessionKeyspace = ${CASSANDRA_KEYSPACE}
    request {
      consistency = "LOCAL_QUORUM"
      serialConsistency = "LOCAL_SERIAL"
      timeout = 5s
    }
  }

  profiles {
    analytics {
      basic.request.consistency = "ONE"
      basic.request.timeout = 30s
      basic.request.pageSize = 1000
    }
    critical {
      basic.request.consistency = "QUORUM"
      basic.request.serialConsistency = "SERIAL"
      basic.request.timeout = 2s
    }
  }
}
```

- A profile declares only what it overrides; unset keys fall back to the driver's default profile,
  which is what the root `basic.request` section configured.
- `contactPoints` cannot be overridden per profile — `CassandraConfig.Profile.ProfileBasic`
  overrides it to an empty list, so it always comes from the root section.
- `basic.dc` **can** be overridden per profile; it is applied as
  `CqlSessionBuilder#withLocalDatacenter(profileName, dc)`.
- `profiles.<name>.advanced` accepts the same `advanced.*` keys the root section does, limited to
  the overridable subset (load balancing DC failover, request tracing/warnings, throttler, …).

---

## @CassandraProfile

```java
@Repository
public interface EventRepository extends CassandraRepository {

    @CassandraProfile("analytics")
    @Query("SELECT * FROM events_by_type WHERE type = :type")
    List<Event> findByType(String type);

    @CassandraProfile("critical")
    @Query("SELECT * FROM accounts WHERE id = :id")
    @Nullable
    Account findById(String id);

    @Query("SELECT * FROM events WHERE id = :id")   // default profile
    @Nullable
    Event findById(String id);
}
```

The generator emits `_stmt.setExecutionProfileName("analytics")` on the bound statement builder.

`@CassandraProfile` is `@Target(ElementType.METHOD)` — it cannot be placed on the repository
interface or on a parameter. The name must match a `cassandra.profiles.<name>` section; a typo is
not a compile error, it is a driver `IllegalArgumentException` at execution time.

---

## Per-query overrides without a profile

For one-off tuning that does not deserve a config section, set options directly on a
[`CassandraQuery`](cql-repository-reference.md#manual-queries) executed through `executor()`:

```java
var query = CassandraQuery.named()
    .cql("SELECT * FROM events WHERE bucket = :bucket")
    .bind("bucket", bucket)
    .opts(o -> o
        .consistencyLevel(DefaultConsistencyLevel.ONE)
        .pageSize(1000)
        .timeout(Duration.ofSeconds(30))
        .idempotent(true))
    .build();

var events = repository.executor().queryList(query, eventRowMapper);
```

`CassandraQuery.OptsBuilder` covers `consistencyLevel`, `serialConsistencyLevel`, `pageSize`,
`timeout`, `idempotent` and `tracing`, each mapped onto the matching
`BoundStatementBuilder` setter. Declarative `@Query` methods have no equivalent — use a profile.

---

## Serial consistency and LWT

Lightweight transactions (`IF`, `IF NOT EXISTS`) use the serial consistency level, not the regular
one. Cassandra has no transactions and `CassandraExecutor` exposes no `inTx`; LWT is a
compare-and-set scoped to a single partition.

```hocon
cassandra.basic.request {
  consistency = "LOCAL_QUORUM"
  serialConsistency = "LOCAL_SERIAL"
}
```

```java
@Query("INSERT INTO users(id, email) VALUES (:user.id, :user.email) IF NOT EXISTS")
boolean insertIfNotExists(User user);

@Query("UPDATE users SET email = :email WHERE id = :id IF email = :oldEmail")
boolean updateIfEmailMatches(String id, String email, String oldEmail);
```

`boolean` reads the `[applied]` flag, which Cassandra returns as the first column of an LWT result.
Return a `@Nullable` entity instead if you need the row that lost the race.

- `SERIAL` runs Paxos across all datacenters; `LOCAL_SERIAL` stays in the local DC and is what a
  multi-DC deployment normally wants.
- LWT costs roughly four round trips. Do not use it as a general-purpose write path.
- `defaultIdempotence` matters here: a non-idempotent statement is not retried or speculatively
  executed, which is the safe default for LWT and for counter updates.

---

## Multi-DC

```hocon
cassandra {
  basic {
    dc = ${CASSANDRA_DC}
    request {
      consistency = "LOCAL_QUORUM"
      serialConsistency = "LOCAL_SERIAL"
    }
    loadBalancingPolicy.slowReplicaAvoidance = true
  }
  advanced.loadBalancingPolicy.dcFailover {
    maxNodesPerRemoveDc = 2
    allowForLocalConsistencyLevels = false
  }
}
```

`basic.dc` is applied as `CqlSessionBuilder#withLocalDatacenter`. Remote-DC failover is off by
default; `maxNodesPerRemoveDc` (spelled exactly like that in `CassandraConfig`) enables it, and
`allowForLocalConsistencyLevels` controls whether `LOCAL_*` levels may fail over — leaving it
`false` keeps `LOCAL_QUORUM` honest.

---

## Tuning rules

For a replication factor **N**, `readCL + writeCL > N` gives read-your-writes:

| Read | Write | Guarantee |
|---|---|---|
| `ONE` | `ONE` | none |
| `ONE` | `ALL` | strong, writes fragile |
| `QUORUM` | `QUORUM` | strong, balanced — the usual choice |
| `LOCAL_QUORUM` | `LOCAL_QUORUM` | strong within the local DC |
| `LOCAL_ONE` | `LOCAL_QUORUM` | eventually consistent reads, durable writes |

Raise `pageSize` for analytics scans and lower `timeout` for user-facing reads; both belong in a
profile rather than in the root section when only some queries need them.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Consistency setting has no effect | key is `basic.request.consistency`, not `basic.consistency` |
| Profile timeout ignored | the profile must repeat the full path, `basic.request.timeout = …` |
| `IllegalArgumentException: Unknown profile '<name>'` | `@CassandraProfile` name has no `cassandra.profiles.<name>` section |
| `UnavailableException: Not enough replicas available` | consistency exceeds live replicas — lower it, or fix the ring |
| `ReadTimeoutException` / `WriteTimeoutException` | raise `basic.request.timeout`, lower consistency, or reduce `pageSize` |
| `WriteTimeoutException` with `writeType=CAS` | LWT contention on one partition — reshape the key or drop the LWT |
| Retries never happen | statements default to non-idempotent; set `basic.request.defaultIdempotence = true` only where it is safe |

---

## See Also

- [Cassandra Config Reference](cassandra-config-reference.md)
- [CQL Repository Reference](cql-repository-reference.md)
