# Docker Compose for Kora 2.0

**Purpose:** running a Kora 2.0 service together with its dependencies as a local or CI
environment.

> **Compose is not the black-box test harness.** In the migrated Kora 2.0 examples the black-box
> tests are driven by Testcontainers, which builds the image and manages the lifecycle from inside
> the JVM. Compose files sit alongside them for a different job: bringing up Postgres/Kafka/Redis
> so the service can be run with `./gradlew run`, or standing a whole stack up in CI. Use the right
> one for the job — see [testcontainers-reference.md](testcontainers-reference.md) for the test path.

## Contents

1. [When Compose earns its place](#when-compose-earns-its-place)
2. [App + PostgreSQL + Flyway](#app--postgresql--flyway)
3. [App + Kafka](#app--kafka)
4. [App + Cassandra + Redis](#app--cassandra--redis)
5. [Health checks](#health-checks)
6. [Driving tests against a Compose stack](#driving-tests-against-a-compose-stack)
7. [Best practices](#best-practices)
8. [Troubleshooting](#troubleshooting)

---

## When Compose earns its place

**Compose is the better tool when:**
- you want the dependencies up so you can run the service from the IDE or `./gradlew run`
- the stack has more moving parts than a test should own (several services, a proxy, a UI)
- you are reproducing a production-like topology by hand

**Testcontainers is the better tool when:**
- the containers exist only for the duration of a test run
- CI must not depend on an out-of-band `up` step having succeeded
- each suite needs its own isolated instance

Modern Compose ignores the top-level `version:` key; the migrated examples omit it, and so do the
files below.

---

## App + PostgreSQL + Flyway

Mirrors the layout the migrated examples use: Postgres, a one-shot Flyway container for the schema,
and the application, with both Kora ports published.

```yaml
services:
  postgres:
    image: postgres:16.4-alpine
    restart: unless-stopped
    ports:
      - '5432:5432'
    environment:
      POSTGRES_DB: postgres
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  flyway:
    image: flyway/flyway:10.2-alpine
    restart: "no"
    command: -url=jdbc:postgresql://postgres:5432/postgres -schemas=public -user=postgres -password=postgres -connectRetries=60 migrate
    volumes:
      - ./src/main/resources/db/migration:/flyway/sql
    depends_on:
      postgres:
        condition: service_healthy

  application:
    image: my-service
    build: .
    restart: unless-stopped
    ports:
      - '8080:8080'
      - '8085:8085'
    environment:
      POSTGRES_JDBC_URL: jdbc:postgresql://postgres:5432/postgres
      POSTGRES_USER: postgres
      POSTGRES_PASS: postgres
    depends_on:
      - postgres
      - flyway
```

The environment variable **names** are not conventions — they are whatever the application's config
substitutes:

```hocon
httpServer {
  port = 8080
  system.port = 8085
}

jdbc {
  jdbcUrl = ${POSTGRES_JDBC_URL}
  username = ${POSTGRES_USER}
  password = ${POSTGRES_PASS}
}
```

Note `jdbc`, not the Kora 1.x `db`. A stale `db { ... }` section leaves the container dying on
`ConfigValueException: Config expected value, but got null at path: 'ROOT.jdbc.username'`.

Running the migration in its own container is worth doing even when the service also ships
`database-flyway`: it separates "the schema is wrong" from "the application is wrong", and it is the
shape a horizontally scaled deployment needs anyway, since concurrent replicas migrating on startup
race each other.

---

## App + Kafka

A Kafka broker advertises different addresses to different networks, and getting that wrong is the
one Compose mistake that fails silently. The migrated example runs `apache/kafka-native` in KRaft
mode with two plaintext listeners — one for the host, one for the Compose network:

```yaml
services:
  kafka:
    image: apache/kafka-native:4.1.0
    restart: unless-stopped
    ports:
      - '9092:9092'
      - '9093:9093'
    environment:
      KAFKA_KRAFT_MODE: "true"
      KAFKA_PROCESS_ROLES: controller,broker
      KAFKA_NODE_ID: 1
      KAFKA_CONTROLLER_QUORUM_VOTERS: "1@localhost:9093"
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,PLAINTEXT_DOCKER://kafka:29092,CONTROLLER://0.0.0.0:9093
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_DOCKER:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092,PLAINTEXT_DOCKER://kafka:29092
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0
      CLUSTER_ID: "123"
    healthcheck:
      test: nc -z localhost 9092 || exit 1
      interval: 3s
      timeout: 10s
      retries: 5
      start_period: 10s

  application:
    image: my-service
    build: .
    restart: unless-stopped
    ports:
      - '8080:8080'
      - '8085:8085'
    environment:
      KAFKA_BOOTSTRAP: kafka:29092      # the in-network listener, NOT localhost:9092
    depends_on:
      kafka:
        condition: service_healthy
```

```yaml
kafka:
  listener:
    user:
      topics: "users"
      driverProperties:
        bootstrap.servers: ${KAFKA_BOOTSTRAP}
        group.id: "users-gi"
        auto.offset.reset: "earliest"
  publisher:
    task:
      topic: "tasks"
      driverProperties:
        bootstrap.servers: ${KAFKA_BOOTSTRAP}
```

Give the application the **in-network** advertised listener (`kafka:29092`). Hand it
`localhost:9092` and the bootstrap connection succeeds, the broker returns metadata pointing at
`localhost`, and the consumer then quietly receives nothing — no error, no log line worth reading.

---

## App + Cassandra + Redis

```yaml
services:
  cassandra:
    image: cassandra:4.1.4
    restart: unless-stopped
    ports:
      - '9042:9042'
    environment:
      JVM_OPTS: -Dcassandra.skip_wait_for_gossip_to_settle=0 -Dcassandra.initial_token=0
      CASSANDRA_ENDPOINT_SNITCH: GossipingPropertyFileSnitch
      CASSANDRA_DC: datacenter1
      CASSANDRA_NUM_TOKENS: 1
    healthcheck:
      test: ["CMD", "cqlsh", "-e", "DESCRIBE KEYSPACES"]
      interval: 10s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7.2.4
    restart: unless-stopped
    ports:
      - '6379:6379'
    command: redis-server

  application:
    image: my-service
    build: .
    restart: unless-stopped
    ports:
      - '8080:8080'
      - '8085:8085'
    environment:
      CASSANDRA_CONTACT_POINTS: cassandra:9042
      CASSANDRA_USER: cassandra
      CASSANDRA_PASS: cassandra
      CASSANDRA_DC: datacenter1
      CASSANDRA_KEYSPACE: petshop
      REDIS_URL: redis://redis:6379/0
      REDIS_USER: default
      REDIS_PASS: redis
    depends_on:
      - cassandra
      - redis
```

Cassandra needs a generous `start_period`; it routinely takes longer than a minute to accept CQL.

---

## Health checks

The application health check is the same probe the Testcontainers wait strategy uses, on the same
system port:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8085/system/readiness"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 30s
```

Two caveats:

- `curl` must exist in the image. `eclipse-temurin:*-jre-jammy` has it; the GraalVM native runtime
  stage (`ubuntu:noble-*`) may not, and a slim/distroless base certainly will not. A health check
  that always fails because the binary is missing looks exactly like an application that never
  becomes ready.
- Use `/system/readiness` for `depends_on: condition: service_healthy`, and `/system/liveness` for
  restart policies. Readiness answers 503 while a dependency is still unreachable; liveness only
  reports whether the process itself is functioning.

Raise `start_period` for a native image or a service with slow migrations rather than raising
`retries` — during `start_period` a failing check does not count against the retry budget.

---

## Driving tests against a Compose stack

If the stack is already up (started by CI, or by a `docker compose up -d` in a Gradle task), the
test only needs the base URL:

```java
class ComposeApiTest {

    private static final String APP_URL = System.getProperty("test.app.url", "http://localhost:8080");

    @Test
    void shouldCreateUser() throws Exception {
        var request = HttpRequest.newBuilder()
                .POST(HttpRequest.BodyPublishers.ofString("{\"email\":\"test@example.com\"}"))
                .uri(URI.create(APP_URL + "/users"))
                .header("Content-Type", "application/json")
                .timeout(Duration.ofSeconds(5))
                .build();

        var response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());
        assertEquals(201, response.statusCode());
    }
}
```

Gate the suite on readiness rather than on a sleep:

```bash
#!/usr/bin/env bash
set -euo pipefail

docker compose -f docker-compose.test.yml up -d
trap 'docker compose -f docker-compose.test.yml down -v' EXIT

for _ in $(seq 1 60); do
    if curl -sf http://localhost:8085/system/readiness >/dev/null; then
        ./gradlew test
        exit 0
    fi
    sleep 2
done

echo "application never became ready" >&2
docker compose -f docker-compose.test.yml logs application
exit 1
```

Testcontainers also has a module that starts a Compose file from inside the JVM, but its entry-point
class was renamed across Testcontainers versions and the migrated Kora corpus does not use it —
check the class name against the version you declare before adopting it, or stay on the
`GenericContainer`/`AppContainer` path, which the corpus does exercise.

---

## Best practices

1. **Pin image tags** — `postgres:16.4-alpine`, never `:latest`.
2. **`depends_on` with `condition: service_healthy`**, not bare `depends_on`, which waits only for
   the container to be created.
3. **Publish both Kora ports** (`8080`, `8085`) — without `8085` there is no probe and no `/metrics`.
4. **Name environment variables after the application's `${VAR}` keys**, and keep those keys on 2.0
   names (`httpServer.port`, `httpServer.system.port`, `jdbc`).
5. **`down -v`** between runs so a stale volume does not carry a previous schema forward.
6. **Do not point a container at `localhost`** for a sibling service — use the service name.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| App container restarts in a loop | Config error at startup — `docker compose logs application`, look for `ConfigValueException` |
| Health check never passes, app log looks fine | `curl` missing from the image, or the check points at 8080 instead of 8085 |
| App is "healthy" but nothing answers on the expected port | Stale 1.x port keys ignored; the servers fell back to 8080/8085 |
| Kafka consumer receives nothing, no errors | App given the host-facing advertised listener instead of the in-network one |
| `Unsupported Database: PostgreSQL` at startup | `database-flyway` ships `flyway-core` only; add `org.flywaydb:flyway-database-postgresql` |
| `UnsupportedClassVersionError` | Base image below JRE 25 |

---

## Related

- [testcontainers-reference.md](testcontainers-reference.md) — the test-owned container path
- [blackbox-integration-reference.md](blackbox-integration-reference.md) — E2E patterns
- [docker-reference.md](docker-reference.md) — the images these services run
