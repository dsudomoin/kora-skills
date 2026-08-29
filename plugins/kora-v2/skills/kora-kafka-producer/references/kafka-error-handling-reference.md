# Kafka Producer Error Handling Reference (Kora 2.0)

What a `@KafkaPublisher` method throws, when, and how to handle it. Consumer-side error handling
(`KafkaSkipRecordException`, deserialization failures, the trailing `Exception` parameter) belongs to
the `kora-kafka-consumer` skill.

## Contents

- [Which exception, when](#which-exception-when)
- [`KafkaPublishException`](#kafkapublishexception)
- [Serialization errors](#serialization-errors)
- [Blocking sends](#blocking-sends)
- [Future / CompletionStage sends](#future--completionstage-sends)
- [Callback parameter](#callback-parameter)
- [Startup failures](#startup-failures)
- [Transaction failures](#transaction-failures)
- [Dead-letter pattern](#dead-letter-pattern)
- [Reliability configuration](#reliability-configuration)

## Quick Navigation

- [Producer Reference](kafka-producer-reference.md) — signatures and config
- [Serialization Reference](kafka-serialization-reference.md) — `@Json`, `@Tag`
- [Transactions Reference](kafka-transactions-reference.md) — abort semantics

---

## Which exception, when

The generated `$X_Impl` serializes **before** the try/catch and sends **inside** it, so the two
failure classes surface differently:

| Failure | `void` / `RecordMetadata` method | `Future` / `CompletionStage` method |
|---|---|---|
| key or value serialization | `org.apache.kafka.common.errors.SerializationException`, thrown directly | thrown directly — the future is never returned |
| broker / transport / timeout | `KafkaPublishException` with the real cause in `getCause()` | future completes exceptionally with the raw Kafka exception |
| `RuntimeException` raised by the producer itself | rethrown as-is (the generated catch unwraps `ExecutionException` and rethrows a `RuntimeException` cause unchanged) | future completes exceptionally |
| thread interrupted while blocking | `KafkaPublishException` wrapping `InterruptedException` | not applicable |

So: a serialization bug never produces a `KafkaPublishException`, and an async method never throws
one.

## `KafkaPublishException`

```java
package io.koraframework.kafka.common.exceptions;

public final class KafkaPublishException extends org.apache.kafka.common.KafkaException {
    public KafkaPublishException(Throwable cause) { … }
}
```

It is unchecked, carries no message of its own, and always wraps a cause. Always log or inspect
`getCause()` — the top frame alone says nothing about what went wrong.

## Serialization errors

`JsonKafkaSerializer` wraps anything the generated `JsonWriter` throws into
`org.apache.kafka.common.errors.SerializationException` with the message
`Unable to serialize into json`. A hand-written `Serializer<T>` should do the same so callers have
one type to catch.

Since serialization happens before the record is handed to the producer, a serialization failure
means **nothing was sent** — no partial write, no retry needed.

## Blocking sends

```java
import org.apache.kafka.common.errors.SerializationException;
import io.koraframework.kafka.common.exceptions.KafkaPublishException;

@Component
public final class MessageSender {

    private static final Logger log = LoggerFactory.getLogger(MessageSender.class);

    private final MyPublisher publisher;

    public MessageSender(MyPublisher publisher) {
        this.publisher = publisher;
    }

    public void send(String event) {
        try {
            publisher.send(event);
        } catch (SerializationException e) {
            log.error("Cannot serialize event, dropping it", e);
            throw e;
        } catch (KafkaPublishException e) {
            log.error("Publish failed", e.getCause());
            throw e;
        }
    }
}
```

Remember that `void send(...)` **blocks** until the broker acknowledges. A publisher call inside a
request handler adds the broker round-trip to the request latency; that is usually what you want for
correctness, but it is not free.

## Future / CompletionStage sends

```java
@KafkaPublisher("kafka.producer.myPublisher")
public interface MyPublisher {
    @Topic(".topic")
    CompletionStage<RecordMetadata> sendStage(String value);
}
```

```java
publisher.sendStage(event)
    .thenAccept(meta -> log.info("sent to {}-{}@{}", meta.topic(), meta.partition(), meta.offset()))
    .exceptionally(e -> {
        log.error("Publish failed", e);
        return null;
    });
```

Nothing on this path throws `KafkaPublishException`. If you never consume the returned future, a
send failure is invisible: attach a handler, or use a blocking signature.

## Callback parameter

A `Callback` parameter works with every return type. The generated code calls the telemetry
observation first and your callback second, and — on a blocking signature — still blocks on the
result afterwards:

```java
@Topic(".topic")
void send(String value, Callback callback);
```

Use it for per-record bookkeeping, not as a substitute for handling the return value.

## Startup failures

Producer construction happens during graph init. A bad `driverProperties` value fails there with:

```
Kafka publisher '<config path>' failed to start: <cause>; check publisher config,
broker availability, credentials, and TLS/SASL settings
```

An unreachable broker is **not** a startup failure — `KafkaProducer` connects lazily, so the
application comes up and the first send fails or times out instead. Do not use "the app started" as
evidence that the brokers are reachable.

## Transaction failures

Any exception escaping an `inTx` / `withTx` lambda aborts the transaction and is rethrown unchanged:

```java
publisher.inTx(producer -> {
    producer.send("v1");
    producer.send("v2");
    throw new IllegalStateException("nope");   // both records are aborted
});
```

Downstream consumers only skip aborted records when they run with
`isolation.level = read_committed`; with the default `read_uncommitted` they will see the aborted
records.

A `KafkaException` thrown by the commit or abort itself removes the producer from the pool and
closes it, then rethrows — the transaction is lost and the caller must decide whether to retry.
`begin()` throws `org.apache.kafka.common.errors.TimeoutException` when the pool has been at
`maxPoolSize` for `maxWaitTime`.

## Dead-letter pattern

Kora has no built-in producer-side dead letter; route failures with a second publisher:

```java
@KafkaPublisher("kafka.producer.deadLetter")
public interface DeadLetterPublisher {
    @Topic(".topic")
    void send(String key, String payload, Headers headers);
}
```

Put the failure reason in headers rather than mangling the payload, so the original bytes stay
replayable.

## Reliability configuration

```hocon
kafka.producer.myPublisher.driverProperties {
  "bootstrap.servers": ${KAFKA_BOOTSTRAP}
  "acks": "all"
  "enable.idempotence": true       # no duplicate appends on retry
  "retries": 2147483647
  "delivery.timeout.ms": 120000    # the real deadline for a blocking send
}
```

`delivery.timeout.ms` bounds how long a blocking `void` / `RecordMetadata` send can sit before it
fails — set it deliberately if the publisher is called from a request path.

Turn telemetry on, or these failures are invisible:

```hocon
kafka.producer.myPublisher.telemetry {
  logging.enabled = true   # defaults to false
  metrics.enabled = true   # defaults to false
}
```

With metrics on, failures appear as `messaging.client.sent.messages` and
`messaging.client.operation.duration` carrying a non-empty `error.type` tag.
