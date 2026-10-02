# Kafka Transactions Reference (Kora 2.0)

Atomic multi-record sends with `io.koraframework.kafka.common.producer.TransactionalPublisher`.

## Contents

- [Declaring a transactional publisher](#declaring-a-transactional-publisher)
- [Configuration](#configuration)
- [The `TransactionalPublisher` API](#the-transactionalpublisher-api)
- [Java usage](#java-usage)
- [Kotlin usage](#kotlin-usage)
- [Consume-transform-produce](#consume-transform-produce)
- [How the pool behaves](#how-the-pool-behaves)
- [Atomicity with a database](#atomicity-with-a-database)
- [Broker requirements and limits](#broker-requirements-and-limits)

## See Also

- [Producer Reference](kafka-producer-reference.md) — signatures, generated types, config
- [Error Handling](kafka-error-handling-reference.md) — abort paths and exception types

---

## Declaring a transactional publisher

Two interfaces: the payload publisher, and a second `@KafkaPublisher` extending
`TransactionalPublisher<P>` over it. The migrated examples nest the payload publisher inside the
transactional one so both live in a single file:

```java
import io.koraframework.kafka.common.annotation.KafkaPublisher;
import io.koraframework.kafka.common.annotation.KafkaPublisher.Topic;
import io.koraframework.kafka.common.producer.TransactionalPublisher;

@KafkaPublisher("kafka.producer.myTransactional")
public interface MyTransactionalPublisher
        extends TransactionalPublisher<MyTransactionalPublisher.TopicPublisher> {

    @KafkaPublisher("kafka.producer.myPublisher")
    interface TopicPublisher {
        @Topic("kafka.producer.myTopic")
        void send(String value);
    }
}
```

Two side-by-side interfaces are equally valid; nesting is purely a file-layout choice.

Rules enforced by the processor:

- The transactional interface must extend **exactly one** `TransactionalPublisher<P>` and nothing else.
- `P` must itself be annotated `@KafkaPublisher`, otherwise:
  `TransactionalPublisher argument is not annotated with @KafkaPublisher`.
- The transactional interface declares **no methods of its own** — it inherits `begin`, `inTx` and
  `withTx`. A stray send method there fails the build in the generated code:
  `$MyTx_Impl is not abstract and does not override abstract method sendDirect(String)`.

## Configuration

The transactional interface's `@KafkaPublisher` path is mapped to
`KafkaPublisherConfig.TransactionConfig`, which declares **only**:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `idPrefix` | `String` | `"kora-app-"` | `transactional.id` = `<idPrefix>-<random UUID>` |
| `maxPoolSize` | `int` | `10` | maximum pooled transactional producers |
| `maxWaitTime` | `Duration` | `10s` | how long `begin()` waits for a free pooled producer |

```hocon
kafka {
  producer {
    myTransactional {
      idPrefix = "order-service"
      maxPoolSize = 10
      maxWaitTime = "10s"
    }
    myPublisher {
      driverProperties {
        "bootstrap.servers": ${KAFKA_BOOTSTRAP}
        "acks": "all"
      }
    }
    myTopic { topic = "my-topic" }
  }
}
```

> **The transactional section takes no `driverProperties`.** Transactional producers are built from
> the *wrapped* publisher's `driverProperties` plus a generated `transactional.id`. A
> `driverProperties` block under the transactional section is an unknown key: it is ignored without
> a warning, and the producer silently uses the wrapped publisher's brokers instead of the ones you
> wrote there.

`transactional.id` is set by the generated module as `config.idPrefix() + "-" + UUID.randomUUID()`,
so each pooled producer is unique and the prefix should identify the *service*, not the instance.

## The `TransactionalPublisher` API

```java
public interface TransactionalPublisher<P> {

    Transaction<? extends P> begin();

    <E extends Throwable> void inTx(TransactionalConsumer<P, E> callback) throws E;
    <E extends Throwable, R> R inTx(TransactionalFunction<P, E, R> callback) throws E;

    <E extends Throwable> void withTx(TransactionConsumer<P, E> callback) throws E;
    <E extends Throwable, R> R withTx(TransactionFunction<P, E, R> callback) throws E;

    interface Transaction<P> extends AutoCloseable {
        P publisher();
        Producer<byte[], byte[]> producer();
        void sendOffsetsToTransaction(Map<TopicPartition, OffsetAndMetadata> offsets,
                                      ConsumerGroupMetadata groupMetadata);
        void abort(@Nullable Throwable t);
        void abort();
        void flush();
        @Override void close();
    }
}
```

| Method | Hands the lambda | Use it for |
|---|---|---|
| `inTx` | the typed publisher `P` | ordinary "send several records atomically" |
| `withTx` | the whole `Transaction<? extends P>` | when you also need `producer()`, `flush()` or `sendOffsetsToTransaction` |

Semantics for all four: the transaction is begun before the lambda, **committed on `close()`** when
the lambda returns, and **aborted** when it throws (the original exception is rethrown). Both come
in a consumer form (returns nothing) and a function form (returns a value).

The `Transaction` holds a state (`INIT` / `COMMIT` / `ABORT`). `abort()` on a transaction that is no
longer active throws `IllegalStateException`; `close()` after a commit or an abort is a no-op, which
is what makes `try (var tx = publisher.begin())` plus an explicit `tx.abort()` safe.

## Java usage

```java
@Component
public final class MyService {

    private final MyTransactionalPublisher publisher;

    public MyService(MyTransactionalPublisher publisher) {
        this.publisher = publisher;
    }

    // lambda style — commit on return, abort on throw
    public void sendBoth() {
        publisher.inTx(producer -> {
            producer.send("value1");
            producer.send("value2");
        });
    }

    // returning a value
    public int sendAndCount() {
        return publisher.inTx(producer -> {
            producer.send("value1");
            return 1;
        });
    }

    // manual style — commit happens in close(), abort() opts out
    public void sendManual(boolean somethingBad) {
        try (var tx = publisher.begin()) {
            tx.publisher().send("value");
            if (somethingBad) {
                tx.abort();   // close() will then do nothing
            }
        }
    }
}
```

## Kotlin usage

`inTx` and `withTx` are each overloaded (consumer + function), so Kotlin cannot infer the SAM type
from a bare lambda. Name it explicitly — this is what the migrated Kotlin example does:

```kotlin
import io.koraframework.kafka.common.producer.TransactionalPublisher.TransactionalConsumer

publisher.inTx(TransactionalConsumer<MyTransactionalPublisher.TopicPublisher, RuntimeException> { producer ->
    producer.send("""{"username":"Foo"}""")
    producer.send("""{"username":"Bar"}""")
})
```

The second type argument is the checked exception the lambda may throw; use `RuntimeException` when
it throws nothing checked. `begin()` works with `use { }` without any SAM ceremony:

```kotlin
publisher.begin().use { tx ->
    tx.publisher().send("value")
    if (somethingBad) tx.abort()
}
```

## Consume-transform-produce

`withTx` exposes `sendOffsetsToTransaction`, which is what makes read-process-write exactly-once
possible: the consumer offsets are committed inside the same Kafka transaction as the produced
records.

```java
publisher.withTx(tx -> {
    for (var record : records) {
        tx.publisher().send(transform(record.value()));
    }
    tx.sendOffsetsToTransaction(offsets, consumer.groupMetadata());
});
```

The consuming side must run with `enable.auto.commit = false`, and downstream consumers need
`isolation.level = read_committed` to skip aborted records — both are consumer-side settings, see
the `kora-kafka-consumer` skill.

## How the pool behaves

`TransactionalPublisherImpl` keeps a pool of initialised transactional producers:

- `begin()` takes a producer from the pool, or creates one (up to `maxPoolSize`) and calls
  `initTransactions()` on it, then `beginTransaction()`.
- Over `maxPoolSize`, `begin()` waits up to `maxWaitTime` and then throws
  `org.apache.kafka.common.errors.TimeoutException`.
- Commit or abort returns the producer to the pool; a `KafkaException` during commit/abort removes
  it from the pool and closes it, so a poisoned producer is never reused.
- After the component is released, `begin()` throws `IllegalStateException`.

Because a producer is held for the whole lambda, long transactions consume pool slots. Keep the
work inside `inTx` short and free of network calls to other systems.

## Atomicity with a database

A Kafka transaction is atomic **only** across the Kafka sends inside it. It does not make a database
write and a Kafka send atomic together — they are separate systems with separate transaction
managers, and Kora has no two-phase commit bridge.

Use the **transactional outbox**: write the event into an outbox table inside the same database
transaction as the state change, then have a relay read the outbox and publish it (the relay may use
a `TransactionalPublisher` to publish batches atomically). Never nest a Kafka transaction inside a
JDBC transaction and assume they commit together.

## Broker requirements and limits

- Transactions need a Kafka broker with transaction support and a replicated `__transaction_state`
  topic; on a single-broker dev cluster set `transaction.state.log.replication.factor = 1` and
  `transaction.state.log.min.isr = 1`.
- `transactional.id` must be unique per producer instance — Kora guarantees this by appending a
  random UUID to `idPrefix`.
- Transaction duration is bounded by the broker's `transaction.timeout.ms`; exceeding it fences the
  producer and the commit fails.
- Concurrency is bounded by `maxPoolSize`.
