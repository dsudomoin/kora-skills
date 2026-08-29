# Kafka Serialization Reference (Kora 2.0)

How Kora resolves a `org.apache.kafka.common.serialization.Serializer<T>` for every key and value of
a `@KafkaPublisher` method. Consumer-side deserialization mirrors these rules — see the
`kora-kafka-consumer` skill.

## Contents

- [How serializer selection works](#how-serializer-selection-works)
- [JSON with `@Json`](#json-with-json)
- [Custom serializers with `@Tag`](#custom-serializers-with-tag)
- [`toByteArray` no longer throws `IOException`](#tobytearray-no-longer-throws-ioexception)
- [Serializers Kora supplies](#serializers-kora-supplies)
- [Common issues](#common-issues)

---

## How serializer selection works

The annotation processor inspects each key/value parameter (or `ProducerRecord` type argument) and
adds a `Serializer<T>` parameter to the generated publisher constructor. Resolution order:

1. **`@Json` on the parameter** → `KafkaSerializersModule.jsonKafkaSerializer(JsonWriter<T>)`, itself
   tagged `@Json`. Needs `io.koraframework:json-common` and `@Json` on the DTO so a `JsonWriter<T>`
   is generated.
2. **`@Tag(SomeTag.class)` on the parameter** → the `Serializer<T>` `@Component` bound under that tag.
3. **Neither** → the untagged `Serializer<T>` for that type.

Missing at step 3 → a graph error naming `Serializer<YourType>`, at compile time, not at runtime.

Never put `key.serializer` / `value.serializer` in `driverProperties`. `AbstractPublisher` always
builds the `KafkaProducer` with `ByteArraySerializer` on both sides and calls the resolved
`Serializer<T>` itself, passing the record headers:
`serializer.serialize(topic, headers, value)`.

---

## JSON with `@Json`

Annotate the DTO and the parameter:

```java
import io.koraframework.json.common.annotation.Json;
import io.koraframework.kafka.common.annotation.KafkaPublisher;
import io.koraframework.kafka.common.annotation.KafkaPublisher.Topic;

@KafkaPublisher("kafka.producer.myPublisher")
public interface TopicJsonPublisher {

    @Json
    record MyEvent(String username, int code) {}

    @Topic("kafka.producer.myTopic")
    void send(@Json MyEvent value);
}
```

Kotlin — the annotation goes on the parameter, and the DTO is a `data class`:

```kotlin
@KafkaPublisher("kafka.producer.myPublisher")
interface TopicJsonPublisher {

    @Json
    data class MyEvent(val username: String, val code: Int)

    @Topic("kafka.producer.myTopic")
    fun send(@Json value: MyEvent)
}
```

Inside a `ProducerRecord` the annotation is a **type-use** annotation on the type argument:

```java
void send(ProducerRecord<String, @Json MyEvent> record);
```

```kotlin
fun send(record: ProducerRecord<String, @Json MyEvent>)
```

---

## Custom serializers with `@Tag`

Bind a `Serializer<T>` `@Component` under a tag and name the same tag on the parameter. Using the
generated `JsonWriter<T>` keeps the payload identical to `@Json` while letting you add behaviour:

```java
import org.apache.kafka.common.serialization.Serializer;
import io.koraframework.common.annotation.Component;
import io.koraframework.common.annotation.Tag;
import io.koraframework.json.common.JsonWriter;
import io.koraframework.json.common.annotation.Json;
import io.koraframework.kafka.common.annotation.KafkaPublisher;
import io.koraframework.kafka.common.annotation.KafkaPublisher.Topic;

@KafkaPublisher("kafka.producer.myPublisher")
public interface TopicMapperPublisher {

    @Json
    record MyEvent(String username, int code) {}

    @Tag(MyEvent.class)
    @Component
    class MySerializer implements Serializer<MyEvent> {

        private final JsonWriter<MyEvent> writer;

        public MySerializer(JsonWriter<MyEvent> writer) {
            this.writer = writer;
        }

        @Override
        public byte[] serialize(String topic, MyEvent data) {
            return writer.toByteArray(data);
        }
    }

    @Topic("kafka.producer.myTopic")
    void send(@Tag(MyEvent.class) MyEvent value);
}
```

```kotlin
@KafkaPublisher("kafka.producer.myPublisher")
interface TopicMapperPublisher {

    @Json
    data class MyEvent(val username: String, val code: Int)

    @Tag(MyEvent::class)
    @Component
    class MySerializer(private val writer: JsonWriter<MyEvent>) : Serializer<MyEvent> {
        override fun serialize(topic: String, data: MyEvent): ByteArray = writer.toByteArray(data)
    }

    @Topic("kafka.producer.myTopic")
    fun send(@Tag(MyEvent::class) value: MyEvent)
}
```

The tag class is just an identity — reusing the DTO type as its own tag (as the examples do) avoids
inventing marker classes. The tag on the parameter and the tag on the component must match exactly.

`@Tag` also works as a type-use annotation on a `ProducerRecord` argument, on either side:

```java
void send(ProducerRecord<String, @Tag(MyEvent.class) MyEvent> record);
void send(ProducerRecord<@Tag(String.class) String, String> record);
```

---

## `toByteArray` no longer throws `IOException`

In Kora 1.x `JsonWriter.toByteArray` declared `throws IOException` and the unchecked variant was
`toByteArrayUnchecked`. In 2.0 the `*Unchecked` methods are gone and the plain ones declare no
checked exception. A ported `try/catch (IOException)` is now a **compile error**:

```
error: exception IOException is never thrown in body of corresponding try statement
```

Delete the catch (and the `java.io.IOException` import). If you want to convert failures, catch
nothing — Kora's own `JsonKafkaSerializer` wraps whatever escapes into
`org.apache.kafka.common.errors.SerializationException`, and a custom serializer that throws a
`SerializationException` behaves identically.

---

## Serializers Kora supplies

`KafkaSerializersModule` (pulled in by `KafkaModule`) declares these as `@DefaultComponent`, so a
`@Component` of your own for the same type wins without an ambiguity error:

| Type | Serializer |
|---|---|
| `String` | `StringSerializer` |
| `byte[]` | `ByteArraySerializer` |
| `java.nio.ByteBuffer` | `ByteBufferSerializer` |
| `org.apache.kafka.common.utils.Bytes` | `BytesSerializer` |
| `Double`, `Float`, `Integer`, `Long`, `Short` | matching numeric serializers |
| `java.util.UUID` | `UUIDSerializer` |
| `Void` | `VoidSerializer` |
| any `T` tagged `@Json` | `JsonKafkaSerializer<T>` over the generated `JsonWriter<T>` |

---

## Common issues

| Problem | Fix |
|---|---|
| Graph error naming `Serializer<T>` | Add `@Json` (with `io.koraframework:json-common`) or bind a `@Tag` `Serializer<T>` `@Component` |
| `exception IOException is never thrown…` | Remove the `try/catch (IOException)` around `toByteArray` |
| `Multiple components match Serializer<T>` | Two untagged components for the same type — tag one, or drop `@Component` from the redundant one |
| `SerializationException` at send time | The DTO or the custom serializer failed; the send never reached the broker and no `KafkaPublishException` is thrown |
| Consumer reads garbage | The consumer side must use the mirroring `@Json` / `@Tag` deserializer — see `kora-kafka-consumer` |
| `@Tag` on the parameter has no matching component | The tag class on the parameter and on the `@Component` must be the *same* class literal |
