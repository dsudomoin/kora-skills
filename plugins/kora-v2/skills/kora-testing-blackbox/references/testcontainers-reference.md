# Testcontainers Reference

**Purpose:** Testcontainers usage for black-box testing of a Kora 2.0 application.

> Kora ships **no** Testcontainers wrapper and constrains no Testcontainers version — `kora-bom`
> holds constraints for `io.koraframework:*` artifacts only. The version and the coordinates are
> entirely the application's choice.

## Contents

1. [Coordinates and versions](#coordinates-and-versions)
2. [Lifecycle annotations](#lifecycle-annotations)
3. [PostgreSQL](#postgresql)
4. [Shared network](#shared-network)
5. [Kafka](#kafka)
6. [Wait strategies](#wait-strategies)
7. [Container scope](#container-scope)
8. [Best practices](#best-practices)

---

## Coordinates and versions

The migrated Kora 2.0 examples run **Testcontainers 1.21.4** on JDK 25, with the classic module
names:

```groovy
testImplementation "org.testcontainers:junit-jupiter:1.21.4"
testImplementation "org.testcontainers:testcontainers:1.21.4"
testImplementation "org.testcontainers:postgresql:1.21.4"
testRuntimeOnly    "org.postgresql:postgresql:42.7.3"
```

### Moving to Testcontainers 2.x

Kora's own internal test fixtures build against Testcontainers `2.0.5`, where the database and
broker modules were **renamed**:

| Testcontainers 1.x | Testcontainers 2.x |
|---|---|
| `org.testcontainers:postgresql` | `org.testcontainers:testcontainers-postgresql` |
| `org.testcontainers:kafka` | `org.testcontainers:testcontainers-kafka` |
| `org.testcontainers:cassandra` | `org.testcontainers:testcontainers-cassandra` |

The core module keeps its name: Kora's catalog maps `testcontainers-core` to
`org.testcontainers:testcontainers` at `2.0.5`. `org.testcontainers:junit-jupiter` has **no entry in
that catalog**, and the framework never uses it — its modules pull `testcontainers-core`,
`testcontainers-postgresql`, `testcontainers-kafka` and `testcontainers-cassandra` directly. So this
skill cannot tell you what `junit-jupiter` is called at 2.x: check it against Testcontainers' own
release notes, and resolve its version separately rather than assuming one version property covers
every module.

Pick a line and stay on it. A 1.x coordinate declared at a 2.x version simply does not resolve, and
a mixed set produces `NoSuchMethodError` at test runtime rather than a dependency error.

---

## Lifecycle annotations

- `@Testcontainers` — enables the JUnit 5 extension on the test class
- `@Container` — a field the extension starts and stops
  - `static` field → started once for the whole class, before `@BeforeAll`
  - instance field → a fresh container per test method
  - Kotlin: put it in a `companion object` with `@JvmStatic` (or `@JvmField`), otherwise the
    extension does not see a static field and never starts the container
- `Network.SHARED` — a process-wide Docker network so containers resolve each other by alias

Containers started with `@Container` are already running by the time `@BeforeAll` executes, which
is why `getMappedPort(...)` and `RestAssured.baseURI` assignments belong there and not in a field
initialiser.

---

## PostgreSQL

```java
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

import static org.junit.jupiter.api.Assertions.assertEquals;

@Testcontainers
class UserPersistenceTest {

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("testdb")
            .withUsername("test")
            .withPassword("test");

    @Test
    void shouldSaveAndFind() throws Exception {
        try (Connection conn = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword());
             Statement stmt = conn.createStatement()) {

            stmt.execute("INSERT INTO users (email) VALUES ('test@example.com')");
            ResultSet rs = stmt.executeQuery("SELECT COUNT(*) FROM users");
            rs.next();
            assertEquals(1, rs.getInt(1));
        }
    }
}
```

### Who runs the migrations

In a black-box test the **application container** runs them: `database-flyway` or
`database-liquibase` executes on startup inside the image, against the same PostgreSQL container.
That is the realistic path and the one worth testing — it proves the migration scripts ship inside
the artifact.

Run Flyway from the test JVM only when you need schema control the application does not provide
(seeding a fixture, or dropping between methods), and remember that `database-flyway` bundles
`flyway-core` alone: the dialect artifact (`org.flywaydb:flyway-database-postgresql`) is the
application's responsibility, in the image and in the test classpath alike.

---

## Shared network

`Network.SHARED` is what lets the application container reach infrastructure by hostname.
`AppContainer` is the `GenericContainer<AppContainer>` wrapper from
[blackbox-integration-reference.md](blackbox-integration-reference.md#the-appcontainer-pattern).

```java
@Testcontainers
class SharedNetworkTest {

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine")
            .withNetwork(Network.SHARED)
            .withNetworkAliases("postgres");

    @Container
    static final AppContainer APP = new AppContainer()
            .withNetwork(Network.SHARED)
            .dependsOn(POSTGRES)
            .withEnv("POSTGRES_JDBC_URL", "jdbc:postgresql://postgres:5432/" + POSTGRES.getDatabaseName())
            .withEnv("POSTGRES_USER", POSTGRES.getUsername())
            .withEnv("POSTGRES_PASS", POSTGRES.getPassword());
}
```

Two addresses, two audiences — mixing them up is the single most common black-box failure:

| Consumer | Address | Source |
|---|---|---|
| The **test JVM**, on the Docker host | `localhost:<mapped>` | `POSTGRES.getJdbcUrl()`, `getMappedPort(...)` |
| The **application container**, inside the network | `postgres:5432` | the network alias and the *container* port |

---

## Kafka

The migrated Kora 2.0 examples provision Kafka through
`io.goodforgod:testcontainers-extensions-kafka:0.15.0`, which exposes both views of the bootstrap
address (`paramsInNetwork()` for siblings, host-facing for the test). The full pattern, including
the advertised-listener trap that silently starves a consumer, is in
[blackbox-integration-reference.md](blackbox-integration-reference.md#kafka-integration-test).

The corpus does not exercise `org.testcontainers`' own Kafka container classes against Kora 2.0.
If you use them, verify the class name, the image and the advertised-listener behaviour against the
Testcontainers version you declare — those changed within the 1.x line and again in 2.x. Whatever
you pick, the invariant holds: the application container must bootstrap on the **in-network**
listener, not on the host-mapped one.

---

## Wait strategies

### HTTP wait — the only correct gate for a Kora application

```java
import org.testcontainers.containers.wait.strategy.Wait;

new AppContainer()
        .withExposedPorts(8080, 8085)
        .waitingFor(Wait.forHttp("/system/readiness").forPort(8085).forStatusCode(200));
```

`forPort(8085)` refers to the **container** port; Testcontainers resolves the mapping itself. The
port must appear in `withExposedPorts(...)` or the strategy has nothing to probe.

The overall budget can be set on either side:

```java
withStartupTimeout(Duration.ofSeconds(30));                                  // whole container
waitingFor(Wait.forHttp("/system/readiness").forPort(8085).forStatusCode(200)
        .withStartupTimeout(Duration.ofSeconds(60)));                        // this strategy
```

The migrated examples use 30 s for a JVM image and 50–60 s for a GraalVM native one.

### What not to wait on

- `Wait.forLogMessage(...)` — binds the test to Kora's startup message wording, which is not part
  of the framework's contract and changed between 1.x and 2.0.
- `Wait.forListeningPort()` — the socket binds before the graph finishes initialising, so the test
  starts firing requests at a half-built application.
- The public port `8080` — it comes up independently of readiness; the probe is on `8085`.

### Infrastructure containers

Purpose-built containers bring their own strategies and normally need none from you —
`PostgreSQLContainer` already waits for the database to accept connections. Override only when you
have a reason to.

---

## Container scope

```java
// once per class, shared by every test method - the default choice
@Container
static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine");

// a fresh container per test method - complete isolation, much slower
@Container
PostgreSQLContainer<?> perTest = new PostgreSQLContainer<>("postgres:16-alpine");
```

Prefer the static form and make the *data* unique instead (unique emails, generated ids). Restarting
an application image per method costs far more than a UUID.

---

## Best practices

1. **`@Testcontainers` + `@Container`** for lifecycle; do not hand-roll start/stop unless you also
   hand-roll the cleanup.
2. **Static containers** for speed; unique test data for isolation.
3. **`Network.SHARED` + aliases** so the application resolves infrastructure by hostname.
4. **HTTP readiness wait** for the application container, always on `8085`.
5. **Pin image tags** — `postgres:16-alpine`, never `postgres:latest`.
6. **One Testcontainers line** — 1.x coordinates at a 1.x version, or 2.x at 2.x, never mixed.

---

## Related resources

- [Testcontainers for Java](https://java.testcontainers.org/)
- [Testcontainers PostgreSQL module](https://java.testcontainers.org/modules/databases/postgres/)
- [Testcontainers Kafka module](https://java.testcontainers.org/modules/kafka/)
- [Awaitility](https://github.com/awaitility/awaitility) — async assertions
- [blackbox-integration-reference.md](blackbox-integration-reference.md) — the E2E patterns
- [docker-reference.md](docker-reference.md) — the images these containers run
