# JSON / Machine-Readable Output Reference

## Contents

- [What Kora 2.0 actually ships](#what-kora-20-actually-ships)
- [What ConsoleTextRecordEncoder emits](#what-consoletextrecordencoder-emits)
- [Writing a JSON encoder](#writing-a-json-encoder)
- [Third-party JSON encoders](#third-party-json-encoders)
- [Field naming for aggregators](#field-naming-for-aggregators)

## What Kora 2.0 actually ships

`io.koraframework:logging-logback` contains exactly one `Encoder<ILoggingEvent>`:
**`ConsoleTextRecordEncoder`**, and it emits **text**, not JSON. There is no `KoraJsonEncoder`, no
`JsonRecordEncoder`, no built-in ELK/Datadog/Splunk encoder — do not offer one.

What Kora *does* give you toward machine-readable logs:

| Piece | What it does |
|---|---|
| `StructuredArgument` / `StructuredArgumentWriter` | every structured value is already serialised as JSON, through Jackson 3 |
| `MDC` | MDC values are JSON-typed, not strings |
| `KoraLoggingEvent` | a public `record … implements ILoggingEvent` exposing `koraMdc()` (`Map<String, StructuredArgumentWriter>`) and `span()` (`SpanContext`) |
| `KoraAsyncAppender` | the thing that produces `KoraLoggingEvent`; without it those two extra accessors are simply not there |
| `KoraMdcConverter` / `KoraLoggingMarkerConverter` | `ClassicConverter`s for a `PatternLayout` |
| `json-common` | arrives transitively with `logging-common` (`api project(':json:json-common')`), so `JsonWriter<T>` and the shared `JsonModule.JSON_FACTORY` are on the classpath already |

So: one JSON object per line is not out of the box, but every ingredient is public API and a
30-line encoder covers it.

## What `ConsoleTextRecordEncoder` emits

```
2026-08-22 09:14:02.311 INFO  [kora-undertow-1] io.koraframework.http.server.common.HttpServer.response - traceId=4f0e… spanId=9a1c… orderId="ORD-1" HttpServer responded
	httpResponse={"serverName":"kora-undertow","serverPort":8080,"operation":"GET /pets/{id}","resultCode":"SUCCESS","processingTime":12,"statusCode":200}
```

- Timestamp `yyyy-MM-dd HH:mm:ss.SSS` in **UTC**, level, thread, logger name.
  The abbreviator target is 100 characters, so a name shorter than that (every Kora telemetry
  logger is) is printed in full — do not expect `i.k.h.s.c.HttpServer.response`.
- `traceId=` / `spanId=` only when the event is a `KoraLoggingEvent` with a valid span context.
- Kora MDC entries as `key=<json>`, then SLF4J MDC entries as `key=value`, then the message.
- One indented line per `StructuredArgument` marker, per `StructuredArgument` argument, and per
  SLF4J key/value whose value is a `StructuredArgumentWriter`.
- The stack trace, if any, appended after the record.

This is greppable and machine-parseable per field, but it is not a single JSON document per line.

## Writing a JSON encoder

`ConsoleTextRecordEncoder` is `final`, so it cannot be subclassed — write a sibling. The interface
surface it implements is the complete `Encoder<ILoggingEvent>` contract, and everything below uses
only public Kora and Logback API.

```java
package com.example.logging;

import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.classic.spi.ThrowableProxyUtil;
import ch.qos.logback.core.Context;
import ch.qos.logback.core.encoder.Encoder;
import ch.qos.logback.core.status.Status;
import io.opentelemetry.api.trace.SpanContext;
import io.koraframework.logging.common.arg.StructuredArgument;
import io.koraframework.logging.common.arg.StructuredArgumentWriter;
import io.koraframework.logging.logback.KoraLoggingEvent;
import tools.jackson.core.json.JsonFactory;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

public final class JsonRecordEncoder implements Encoder<ILoggingEvent> {

    private final JsonFactory jsonFactory = new JsonFactory();

    @Override
    public byte[] encode(ILoggingEvent event) {
        var baos = new ByteArrayOutputStream(256);
        try (var gen = this.jsonFactory.createGenerator(baos)) {
            gen.writeStartObject();
            gen.writeStringProperty("@timestamp", Instant.ofEpochMilli(event.getTimeStamp()).toString());
            gen.writeStringProperty("level", event.getLevel().levelStr);
            gen.writeStringProperty("thread", event.getThreadName());
            gen.writeStringProperty("logger", event.getLoggerName());
            gen.writeStringProperty("message", event.getFormattedMessage());

            if (event instanceof KoraLoggingEvent kora) {
                if (kora.span() != SpanContext.getInvalid()) {
                    gen.writeStringProperty("traceId", kora.span().getTraceId());
                    gen.writeStringProperty("spanId", kora.span().getSpanId());
                }
                for (var e : kora.koraMdc().entrySet()) {   // structured MDC: value keeps its JSON type
                    gen.writeName(e.getKey());
                    e.getValue().writeTo(gen);
                }
            }
            for (var e : event.getMDCPropertyMap().entrySet()) {
                gen.writeStringProperty(e.getKey(), e.getValue());
            }
            if (event.getMarkerList() != null) {
                for (var marker : event.getMarkerList()) {
                    if (marker instanceof StructuredArgument sa) {
                        gen.writeName(sa.fieldName());
                        sa.writeTo(gen);
                    }
                }
            }
            if (event.getArgumentArray() != null) {
                for (var arg : event.getArgumentArray()) {
                    if (arg instanceof StructuredArgument sa) {
                        gen.writeName(sa.fieldName());
                        sa.writeTo(gen);
                    }
                }
            }
            if (event.getKeyValuePairs() != null) {
                for (var kv : event.getKeyValuePairs()) {
                    if (kv.value instanceof StructuredArgumentWriter w) {
                        gen.writeName(kv.key);
                        w.writeTo(gen);
                    }
                }
            }
            if (event.getThrowableProxy() != null) {
                gen.writeStringProperty("stack_trace", ThrowableProxyUtil.asString(event.getThrowableProxy()));
            }
            gen.writeEndObject();
        }
        baos.write('\n');
        return baos.toByteArray();
    }

    @Override public byte[] headerBytes() { return new byte[0]; }
    @Override public byte[] footerBytes() { return new byte[0]; }
    @Override public void setContext(Context context) {}
    @Override public Context getContext() { return null; }
    @Override public void addStatus(Status status) {}
    @Override public void addInfo(String msg) {}
    @Override public void addInfo(String msg, Throwable ex) {}
    @Override public void addWarn(String msg) {}
    @Override public void addWarn(String msg, Throwable ex) {}
    @Override public void addError(String msg) {}
    @Override public void addError(String msg, Throwable ex) {}
    @Override public void start() {}
    @Override public void stop() {}
    @Override public boolean isStarted() { return true; }
}
```

Wire it exactly like the built-in encoder — the `KoraAsyncAppender` wrapper is what makes the
`KoraLoggingEvent` branch above fire at all:

```xml
<appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">
    <encoder class="com.example.logging.JsonRecordEncoder"/>
</appender>

<appender name="ASYNC" class="io.koraframework.logging.logback.KoraAsyncAppender">
    <appender-ref ref="STDOUT"/>
</appender>

<root level="INFO">
    <appender-ref ref="ASYNC"/>
</root>
```

Two things to keep right:

- A key collision (an MDC key named `message`, a structured field named `level`) produces a
  duplicate JSON property. Namespace application fields if that is a risk.
- If the encoder is used in a **native image**, register it in the application's own
  `META-INF/native-image/<group>/logback/reflect-config.json` alongside `KoraAsyncAppender` —
  Logback instantiates encoders reflectively from `logback.xml`.

## Third-party JSON encoders

A third-party Logback JSON encoder plugs in the same way: it is just an
`Encoder<ILoggingEvent>` inside the `ConsoleAppender`, still wrapped by `KoraAsyncAppender`.
Before adopting one, know these facts:

- **No third-party JSON encoder appears anywhere in the Kora 2.0 source or in the migrated
  example applications.** Kora neither ships, tests, nor depends on one; a 1.x project that used
  one is not evidence that the combination still works.
- Kora 2.0 pins **Logback `1.6.2`** and SLF4J `2.0.18`. Check the encoder's supported Logback range
  against that specific version before relying on it, and check it on a real run rather than on a
  successful compile.
- A generic encoder knows nothing about `KoraLoggingEvent`. It will render the SLF4J MDC and, if it
  supports SLF4J key/value pairs, the structured arguments' `toString()` — but **not** `koraMdc()`
  and **not** `span()`. Kora MDC values and `traceId`/`spanId` will be missing from the JSON unless
  the encoder is Kora-aware.

If those trade-offs are unacceptable, the 30-line encoder above is the shorter path.

## Field naming for aggregators

Whatever encoder you use, the fields worth standardising across services are the ones Kora already
produces:

| Field | Where it comes from |
|---|---|
| `traceId`, `spanId` | `KoraLoggingEvent.span()` — populated by `KoraAsyncAppender` from `Span.current()` |
| `httpRequest`, `httpResponse` | HTTP server/client telemetry key/value pairs |
| `sqlQuery` | database telemetry key/value pair |
| `listenerConfig`, record fields | Kafka consumer telemetry |
| your own MDC keys | `MDC.put(...)` at the request/message/job entry point |
