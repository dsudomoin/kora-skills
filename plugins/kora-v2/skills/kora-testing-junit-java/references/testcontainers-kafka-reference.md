# Testcontainers Kafka Reference

Integration-testing Kora 2.0 Kafka consumers (`@KafkaListener`) and publishers
(`@KafkaPublisher`) against a real broker.

## Contents

- [Dependencies](#dependencies)
- [Kora 2.0 Kafka config shape](#kora-20-kafka-config-shape)
- [Getting the consumer into the test graph](#getting-the-consumer-into-the-test-graph)
- [Test with the `io.goodforgod` Kafka extension](#test-with-the-iogoodforgod-kafka-extension)
- [Test with a plain Testcontainers container](#test-with-a-plain-testcontainers-container)
- [Verifying asynchronous delivery](#verifying-asynchronous-delivery)
- [Troubleshooting](#troubleshooting)

---

## Dependencies

The migrated Kora 2.0 Kafka examples use the `io.goodforgod` Testcontainers extension, which
brings a broker, topic creation and a test producer/consumer in one annotation:

```groovy
dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")

    annotationProcessor "io.koraframework:annotation-processors"
    testAnnotationProcessor "io.koraframework:annotation-processors"

    implementation "io.koraframework:kafka"

    testImplementation platform("org.junit:junit-bom:$junitVersion")
    testImplementation "org.junit.jupiter:junit-jupiter"
    testImplementation "io.koraframework:test-junit5"
    testImplementation "io.goodforgod:testcontainers-extensions-kafka:0.15.0"
}
```

Plain Testcontainers works too:

```groovy
testImplementation "org.testcontainers:junit-jupiter:1.21.4"
testImplementation "org.testcontainers:kafka:1.21.4"
testImplementation "org.awaitility:awaitility:4.3.0"
```

On Testcontainers 2.x the module is renamed to `org.testcontainers:testcontainers-kafka`; keep
`org.testcontainers:junit-jupiter` on the Testcontainers version, never on the JUnit one.

---

## Kora 2.0 Kafka config shape

There is **no flat `kafka.bootstrapServers` key.** Each `@KafkaListener("<path>")` and each
`@KafkaPublisher("<path>")` reads its own config section at the path given in the annotation, and
the broker address goes into `driverProperties` under the native Kafka client property names.

```java
@Component
public final class UserCreatedConsumer {

    @KafkaListener("kafka.consumer.user-created")
    public void process(@Json @Nullable UserCreatedEvent event, @Nullable Exception exception) { … }
}

@KafkaPublisher("kafka.producer.user-created")
public interface UserCreatedPublisher {

    @Topic("kafka.producer.user-created-topic")
    void send(@Json UserCreatedEvent event);
}
```

```hocon
kafka {
  producer {
    user-created {
      driverProperties {
        "bootstrap.servers": ${?KAFKA_BOOTSTRAP}
      }
      telemetry.logging.enabled = true
    }

    user-created-topic {
      topic = "user-created-events"
    }
  }

  consumer {
    user-created {
      topics = "user-created-events"
      pollTimeout = 250ms
      driverProperties {
        "bootstrap.servers": ${?KAFKA_BOOTSTRAP}
        "group.id": "guide-messaging-kafka-app"
        "auto.offset.reset" = "earliest"
        "enable.auto.commit" = true
      }
      telemetry.logging.enabled = true
    }
  }
}
```

`kafka.consumer` / `kafka.producer` are just the path segments this application chose — nothing in
the framework requires them. What matters is that the config path matches the annotation string
exactly; a mismatched path is read as absent and the consumer never starts.

Read-from-the-beginning depends on the strategy:

| Consumer setup | How to start from the beginning |
|---|---|
| Has a `group.id` (subscribe strategy) | `"auto.offset.reset" = "earliest"` inside `driverProperties` |
| No `group.id` (assign strategy) | `offset = "earliest"` — `KafkaListenerConfig.offset` is `earliest`, `latest` (default) or a `Duration` to shift back from the latest offset, and **only applies to the assign strategy** |

Other useful `KafkaListenerConfig` keys with defaults: `pollTimeout` (5s), `backoffTimeout` (15s),
`threads` (1 — **`0` disables the consumer**), `shutdownWait` (30s), `allowEmptyRecords` (false).
`topics` and `topicsPattern` are mutually exclusive and one of them is required.

Telemetry is off by default (`telemetry.logging.enabled` and `telemetry.metrics.enabled` are
`false`), so enable them in the test config if the test asserts on consumer/publisher metrics or
you want the log line that proves a record was handled.

---

## Getting the consumer into the test graph

A Kafka consumer is normally not a dependency of anything the test injects, so the trimmed graph
drops it and no records are ever consumed. Two source-backed ways to keep it:

**List the generated consumer module.** `@KafkaListener` on a method of `UserCreatedConsumer`
generates the interface `UserCreatedConsumerModule` in the same package; every factory method in a
`modules` entry becomes a graph root:

```java
@KoraAppTest(value = Application.class, modules = UserCreatedConsumerModule.class)
class MessagingKafkaAppTest implements KoraAppTestConfigModifier { … }
```

**Inject the consumer's `Lifecycle` by its generated tag.** The tag is a nested class of that
module named `<Class><Method>Tag`:

```java
@Tag(AutoCommitValueListenerModule.AutoCommitValueListenerProcessTag.class)
@TestComponent
private Lifecycle consumerLifecycle;
```

`@KafkaPublisher` generates `$<Publisher>_PublisherModule`, but a publisher is usually reached
through the service that calls it, so it rarely needs to be listed.

---

## Test with the `io.goodforgod` Kafka extension

```java
@TestcontainersKafka(mode = ContainerMode.PER_RUN, topics = @Topics({ "user-created-events" }))
@KoraAppTest(value = Application.class, modules = UserCreatedConsumerModule.class)
class MessagingKafkaAppTest implements KoraAppTestConfigModifier {

    @ConnectionKafka
    private KafkaConnection connection;

    @TestComponent
    private UserController userController;

    @TestComponent
    private UserService userService;

    @Override
    public KoraConfigModification config() {
        return KoraConfigModification.ofSystemProperty("KAFKA_BOOTSTRAP", connection.params().bootstrapServers());
    }

    @Test
    void createUserPublishesUserCreatedEvent() {
        var consumer = this.connection.subscribe("user-created-events");

        var accepted = this.userController.createUser(new UserRequest("Kafka User", "kafka-user@example.com"));

        assertEquals(202, accepted.code());
        var receivedEvent = consumer.assertReceivedAtLeast(1).get(0);
        assertEquals(accepted.body().id(), receivedEvent.value().asJson().getString("id"));
    }
}
```

`ofSystemProperty` is enough here: the production `application.conf` already reads
`${?KAFKA_BOOTSTRAP}`, so only that one value has to change. That also keeps the rest of the
config — topics, `group.id`, poll timeouts — exactly as production has it, which an inline
`ofString` block would replace wholesale.

`ContainerMode.PER_RUN` reuses one broker for the whole build; Kafka is much slower to start than
PostgreSQL, so this matters.

---

## Test with a plain Testcontainers container

```java
@Testcontainers
@KoraAppTest(value = Application.class, modules = UserCreatedConsumerModule.class)
class KafkaIntegrationTest implements KoraAppTestConfigModifier {

    @Container
    static final KafkaContainer KAFKA =
        new KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.5.0"))
            .withStartupTimeout(Duration.ofSeconds(60));

    @TestComponent
    private UserService userService;

    @Override
    public KoraConfigModification config() {
        return KoraConfigModification.ofSystemProperty("KAFKA_BOOTSTRAP", KAFKA.getBootstrapServers());
    }
}
```

If the production config has no `${?KAFKA_BOOTSTRAP}` placeholder, supply the whole section
inline — and then it must be complete, because `ofString` replaces `application.conf`:

```java
return KoraConfigModification.ofString("""
        kafka {
          consumer.user-created {
            topics = "user-created-events"
            pollTimeout = 250ms
            driverProperties {
              "bootstrap.servers" = ${KAFKA_BOOTSTRAP}
              "group.id" = "test-consumer-group"
              "auto.offset.reset" = "earliest"
              "enable.auto.commit" = true
            }
          }
          producer.user-created.driverProperties {
            "bootstrap.servers" = ${KAFKA_BOOTSTRAP}
          }
          producer.user-created-topic.topic = "user-created-events"
        }
        """)
    .withSystemProperty("KAFKA_BOOTSTRAP", KAFKA.getBootstrapServers());
```

Use a unique `group.id` per test class so offsets from another class do not hide the records you
just produced.

---

## Verifying asynchronous delivery

Consumption happens on the consumer's own threads, so assert with Awaitility, never with
`Thread.sleep`:

```java
Awaitility.await()
    .atMost(Duration.ofSeconds(15))
    .pollExecutorService(Executors.newSingleThreadExecutor())
    .until(() -> this.userService.getUser(userId).isPresent());
```

The migrated examples import `org.testcontainers.shaded.org.awaitility.Awaitility`, which comes
for free with Testcontainers. Adding `org.awaitility:awaitility:4.3.0` explicitly (the version in
Kora's own catalog) and importing `org.awaitility.Awaitility` is the cleaner choice for new code —
shaded packages are an implementation detail of Testcontainers.

`.pollExecutorService(Executors.newSingleThreadExecutor())` matters: Awaitility's default poll
thread interacts badly with virtual-thread-heavy stacks in some setups, and the examples pin an
explicit executor.

Kora 2.0 contracts are synchronous, so the observable side effect is a plain call: a repository
returning the row, a counter, a list on the consumer component. There is no `Mono`/`Flux` to
subscribe to.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Nothing is ever consumed | The consumer was pruned from the subgraph | Add `modules = <Consumer>Module.class`, or inject the tagged consumer `Lifecycle` |
| Consumer starts but sees no records | Config path does not match the `@KafkaListener` string, so the section reads as absent | Make the HOCON path identical to the annotation value |
| Consumer starts at the end of the topic | `auto.offset.reset` not set for a `group.id` consumer | `"auto.offset.reset" = "earliest"` in `driverProperties` |
| `offset = "earliest"` has no effect | The consumer has a `group.id`, so the subscribe strategy is used | Use `auto.offset.reset` instead |
| Consumer never runs at all | `threads = 0` | `threads` must be ≥ 1 |
| Connection refused | `bootstrap.servers` missing in `driverProperties` | There is no flat `kafka.bootstrapServers` key — set it per consumer/producer |
| Container startup timeout | Kafka boots slowly | Raise `withStartupTimeout(...)`; prefer `ContainerMode.PER_RUN` |
| No consumer/publisher metrics or logs | Telemetry defaults to `false` | Enable `telemetry.metrics.enabled` / `telemetry.logging.enabled` for that section |
| Flaky first assertion | Racing the async consumer | Awaitility with a realistic `atMost`, not `Thread.sleep` |
