# Async Patterns Reference

**Artifact:** `io.koraframework:database-cassandra`
**Contract:** `io.koraframework.database.cassandra.mapper.result.CassandraAsyncResultSetMapper`
**Driver:** DataStax Java Driver 4.19.3, `com.datastax.oss.driver.api.core.cql.AsyncResultSet`

## Contents

- [What survived in 2.0](#what-survived-in-20)
- [Java CompletionStage repositories](#java-completionstage-repositories)
- [How the async path is generated](#how-the-async-path-is-generated)
- [CassandraAsyncResultSetMapper](#cassandraasyncresultsetmapper)
- [Composing futures in a service](#composing-futures-in-a-service)
- [Kotlin has no async form](#kotlin-has-no-async-form)
- [Parallelism without async](#parallelism-without-async)
- [Choosing sync or async](#choosing-sync-or-async)

---

## What survived in 2.0

Kora 2.0 contracts are synchronous and run on virtual threads. Cassandra keeps one narrow
exception, and only in Java.

| Signature | Status | Evidence |
|---|---|---|
| `CompletionStage<T>` / `CompletableFuture<T>` (Java) | **supported** | `CassandraRepositoryGenerator#generate` branches on `CommonUtils.isCompletionStage`; the migrated Kora example ships `CassandraCrudAsyncRepository` with a passing `@KoraAppTest` |
| `CassandraAsyncResultSetMapper<T>` | **supported** | public contract in `database-cassandra`, with a diagnostic entry in the module's `kora-module-hints.json` |
| Kotlin `suspend fun` | **rejected at compile time** | `RepositoryBuilder.build` throws before any generator runs |
| Kotlin `Flow<T>` | **not usable** | absent from framework tests and from the migrated Kotlin example; the generated code does not type-check |
| `Mono<T>` / `Flux<T>` | **removed** | no Reactor dependency in `database-cassandra/build.gradle`, no `Mono`/`Flux` handling in the Cassandra generator |
| `ReactiveResultSet`, `CassandraReactiveResultSetMapper` | **removed** | the mapper type no longer exists; the leftover `CassandraTypes.REACTIVE_RESULT_SET` constant has no reference anywhere |

Do **not** add `io.projectreactor:reactor-core` for a Cassandra repository — nothing consumes it.

---

## Java CompletionStage repositories

```java
package com.example;

import io.koraframework.database.cassandra.CassandraRepository;
import io.koraframework.database.cassandra.annotation.EntityCassandra;
import io.koraframework.database.common.annotation.Batch;
import io.koraframework.database.common.annotation.Column;
import io.koraframework.database.common.annotation.Query;
import io.koraframework.database.common.annotation.Repository;
import org.jspecify.annotations.Nullable;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;

@Repository
public interface UserAsyncRepository extends CassandraRepository {

    @EntityCassandra
    record Entity(String id,
                  @Column("value1") int field1,
                  String value2,
                  @Nullable String value3) {}

    @Query("SELECT * FROM entities WHERE id = :id")
    CompletableFuture<Entity> findById(String id);

    @Query("SELECT * FROM entities")
    CompletionStage<List<Entity>> findAll();

    @Query("INSERT INTO entities(id, value1, value2, value3) VALUES (:entity.id, :entity.field1, :entity.value2, :entity.value3)")
    CompletionStage<Void> insert(Entity entity);

    @Query("INSERT INTO entities(id, value1, value2, value3) VALUES (:entity.id, :entity.field1, :entity.value2, :entity.value3)")
    CompletionStage<Void> insertBatch(@Batch List<Entity> entity);

    @Query("DELETE FROM entities WHERE id = :id")
    CompletionStage<Void> deleteById(String id);

    @Query("TRUNCATE entities")
    CompletableFuture<Void> deleteAll();
}
```

Rules:

- The CQL is identical to the synchronous form — only the return type changes.
- `CompletionStage` and `CompletableFuture` are both accepted; the generator appends
  `.toCompletableFuture()` only for the concrete `CompletableFuture` return type.
- `CompletionStage<Void>` is the write/void form. A `@Batch` method must use it — batch methods
  produce no mapped result.
- A single-row lookup resolves to `null` when nothing matched (`CassandraAsyncResultSetMapper.one`
  completes with `null`), so treat `findById(...).join()` as nullable.
- Optional/primitive return shapes are not part of the async path; use `T`, `List<T>` or `Void`.

---

## How the async path is generated

For a `CompletionStage` return type the generator emits, in order:

1. `telemetry().observe(queryContext)` and `currentSession()`;
2. `session.prepareAsync(sql)`;
3. `boundStatementBuilder()`, then `setExecutionProfileName(...)` when `@CassandraProfile` is
   present, then parameter binding;
4. `session.executeAsync(stmt)` chained with `thenCompose(mapper::apply)` — or
   `thenApply(rs -> (Void) null)` for a `Void` payload;
5. `whenComplete(...)` that records the error and ends the observation.

Everything therefore stays on the driver's own I/O threads; nothing blocks a virtual thread on your
behalf, and telemetry spans close when the future completes rather than when the call returns.

---

## CassandraAsyncResultSetMapper

```java
public interface CassandraAsyncResultSetMapper<T> extends Mapping.MappingFunction {

    CompletionStage<T> apply(AsyncResultSet rows);

    static <T> CassandraAsyncResultSetMapper<T> one(CassandraRowMapper<T> rowMapper);
    static <T> CassandraAsyncResultSetMapper<List<T>> list(CassandraRowMapper<T> rowMapper);
}
```

`list(...)` walks pages itself via `AsyncResultSet#hasMorePages` / `fetchNextPage`, so a
`CompletionStage<List<T>>` method fetches the whole result set, not just the first page. Mind the
memory cost on wide scans — bound them with `LIMIT` or `basic.request.pageSize`.

The extension supplies these automatically: a `CompletionStage<List<T>>` method gets `list(...)`,
anything else `one(...)`, in both cases fed by the `CassandraRowMapper<T>` for the entity. Supply
your own only for a non-entity result shape:

```java
@Component
public final class EventPartsAsyncMapper
        implements CassandraAsyncResultSetMapper<Map<Integer, List<EventPart>>> {

    @Override
    public CompletionStage<Map<Integer, List<EventPart>>> apply(AsyncResultSet rows) {
        return collect(rows, new LinkedHashMap<>());
    }

    private CompletionStage<Map<Integer, List<EventPart>>> collect(
            AsyncResultSet rs, Map<Integer, List<EventPart>> acc) {
        for (var row : rs.currentPage()) {
            var part = new EventPart(row.getString(0), row.getInt(1));
            acc.computeIfAbsent(part.field1(), k -> new ArrayList<>()).add(part);
        }
        return rs.hasMorePages()
            ? rs.fetchNextPage().thenCompose(next -> collect(next, acc))
            : CompletableFuture.completedFuture(acc);
    }
}

@Mapping(EventPartsAsyncMapper.class)
@Query("SELECT id, value1 FROM events")
CompletionStage<Map<Integer, List<EventPart>>> findAllParts();
```

The `@Component` rule is the same as for every other mapper: this class is `final` with a no-arg
constructor, so the generated repository would construct it itself; a mapper with constructor
dependencies or a non-`final` class **must** be a component. See
[CQL Repository Reference](cql-repository-reference.md#when-a-mapper-must-be-a-component).

---

## Composing futures in a service

```java
@Component
public final class UserService {

    private final UserAsyncRepository repository;

    public UserService(UserAsyncRepository repository) {
        this.repository = repository;
    }

    public CompletionStage<User> findOrFallback(String id, String fallbackId) {
        return repository.findById(id)
            .thenCompose(user -> user != null
                ? CompletableFuture.completedFuture(user)
                : repository.findById(fallbackId));
    }

    public CompletionStage<Void> replaceAll(List<Entity> entities) {
        return repository.deleteAll()
            .thenCompose(ignored -> repository.insertBatch(entities));
    }
}
```

Never `join()` / `get()` inside a repository or inside another stage's callback — that parks a
driver I/O thread. Either return the stage or join once, at the edge of the call.

---

## Kotlin has no async form

`RepositoryBuilder.build` rejects `suspend` for **every** database backend before choosing a
generator:

```
Repository method is invalid:
  findById

Problem:
  Suspend methods are not supported by the repository generator.
```

Its own hint is Java `StructuredTaskScope`. `Flow<T>` is equally unusable: it appears in no
framework test and in no migrated Kotlin example, and the generator's non-suspend path applies the
selected mapper to a `ResultSet` while the `Flow` branch supplies a `CassandraRowMapper` whose
`apply` takes a `Row` — the generated file does not compile.

The migrated Kora Kotlin Cassandra example has no async repository at all: every method is a plain
`fun`.

Port a 1.x Kotlin repository like this:

| Kora 1.x Kotlin | Kora 2.0 Kotlin |
|---|---|
| `suspend fun findById(id: String): User?` | `fun findById(id: String): User?` |
| `suspend fun findAll(): List<User>` | `fun findAll(): List<User>` |
| `fun findAllFlow(): Flow<User>` | `fun findAll(): List<User>` (page with `LIMIT` / `pageSize`) |
| `suspend fun insert(user: User)` | `fun insert(user: User)` |

Callers lose `withContext` / `Dispatchers.IO` wrappers — the call already runs on a virtual thread.

---

## Parallelism without async

Independent repository calls fan out with `StructuredTaskScope` (a JDK preview API — enable
`--enable-preview` for javac, Kotlin `-Xjvm-enable-preview` plus `-Xjdk-release=N`, and for tests,
`JavaExec` and the production launcher):

```java
Profile loadProfile(String id) throws InterruptedException {
    try (var scope = StructuredTaskScope.open(
            StructuredTaskScope.Joiner.<Object>awaitAllSuccessfulOrThrow())) {
        var user = scope.fork(() -> userRepository.findById(id));
        var orders = scope.fork(() -> orderRepository.findByUser(id));
        scope.join();
        return new Profile(user.get(), orders.get());
    }
}
```

Without preview features, `ExecutorService` over virtual threads gives the same effect with a
plainer API. Java `CompletionStage` repositories remain an option when the pipeline is genuinely
asynchronous end to end.

---

## Choosing sync or async

Prefer synchronous. On virtual threads a blocking Cassandra call costs a parked carrier-free
thread, and you keep readable stack traces, ordinary `try`/`catch`, and telemetry spans that close
where the call sits.

Reach for `CompletionStage` when:

- the result feeds an existing future-based pipeline you do not control;
- you fan out many independent statements and want the driver to interleave them without one thread
  per statement;
- you are already streaming pages with `AsyncResultSet` for a custom mapper.

Do not mix both styles in a single repository interface — split them, as the Kora example does with
`CassandraCrudSyncRepository` and `CassandraCrudAsyncRepository`.

---

## See Also

- [CQL Repository Reference](cql-repository-reference.md)
- [Consistency Reference](consistency-reference.md)
