# KoraAsyncAppender Reference

## Contents

- [What it is and why it is not optional](#what-it-is-and-why-it-is-not-optional)
- [What append() actually does](#what-append-actually-does)
- [Configuration](#configuration)
- [discardingThreshold does nothing here](#discardingthreshold-does-nothing-here)
- [Caller data is unavailable](#caller-data-is-unavailable)
- [Serialisation happens on the appender thread](#serialisation-happens-on-the-appender-thread)
- [Shutdown](#shutdown)
- [Troubleshooting](#troubleshooting)

## What it is and why it is not optional

```java
public final class KoraAsyncAppender extends AsyncAppenderBase<ILoggingEvent> { … }
```

`io.koraframework.logging.logback.KoraAsyncAppender` wraps another appender and hands events to a
worker thread, so application threads do not block on log I/O. That is the ordinary
`AsyncAppenderBase` job. What makes it **required rather than an optimisation** is that it is the
only thing that produces a `KoraLoggingEvent`:

- the Kora `MDC` snapshot (`koraMdc()`), and
- the current OpenTelemetry span context (`span()`)

exist **only** on `KoraLoggingEvent`. Without `KoraAsyncAppender` in the chain,
`ConsoleTextRecordEncoder` receives a plain `ILoggingEvent`, and `traceId`, `spanId` and every Kora
MDC value are absent from the line. Logback's stock `ch.qos.logback.classic.AsyncAppender` does not
substitute for it — it produces no `KoraLoggingEvent` either.

Every migrated example app wraps its console appender with it, in `logback.xml` and
`logback-test.xml` alike.

## What `append()` actually does

```java
@Override
protected void append(ILoggingEvent eventObject) {
    var koraLoggingEvent = new KoraLoggingEvent(
        eventObject.getThreadName(), eventObject.getLoggerName(), …,
        eventObject.getKeyValuePairs(),
        MDC.VALUE.isBound() ? Map.copyOf(MDC.get().values()) : Map.of(),
        Span.current().getSpanContext()
    );
    super.append(koraLoggingEvent);
}
```

Two things to take from this:

1. The MDC and span are captured **eagerly, on the logging thread**, before the event is queued.
   That is what makes them survive the hop to the appender thread.
2. The `MDC.VALUE.isBound()` guard matters: an event logged outside any request/message/job scope
   has no MDC bound, and reading an unbound `ScopedValue` would throw inside the appender and
   silently drop the event. Copy the guard into your own appender/encoder code.

## Configuration

`KoraAsyncAppender` adds no configuration of its own; it accepts what
`ch.qos.logback.core.AsyncAppenderBase` declares:

| Element | Default (`AsyncAppenderBase`) | Effect |
|---|---|---|
| `queueSize` | `256` | Capacity of the `ArrayBlockingQueue` between the logging threads and the worker |
| `neverBlock` | `false` | `false` → a full queue blocks the logging thread (`putUninterruptibly`); `true` → the event is dropped (`offer`) |
| `maxFlushTime` | `1000` (ms) | Time budget for draining the queue on `stop()` |
| `discardingThreshold` | `queueSize / 5` when unset | **No effect here — see below** |

```xml
<appender name="ASYNC" class="io.koraframework.logging.logback.KoraAsyncAppender">
    <appender-ref ref="STDOUT"/>
    <queueSize>8192</queueSize>
    <maxFlushTime>2000</maxFlushTime>
</appender>
```

Sizing: memory is roughly `queueSize × average event size`. Start with the default and raise
`queueSize` only when you can show that logging threads are blocking. Since nothing is ever
discarded, a larger queue changes *how long* a burst is absorbed, not whether records are lost.

Values above are read from Logback's own `AsyncAppenderBase`
(<https://logback.qos.ch/manual/appenders-async-sift.html>); Kora 2.0 pins Logback `1.6.2`.

## `discardingThreshold` does nothing here

`AsyncAppenderBase.append` is

```java
protected void append(E eventObject) {
    if (isQueueBelowDiscardingThreshold() && isDiscardable(eventObject)) return;
    preprocess(eventObject);
    put(eventObject);
}
```

and `AsyncAppenderBase.isDiscardable` **always returns `false`**. The level-based discarding of
`TRACE`/`DEBUG`/`INFO` that the Logback manual describes lives in the subclass
`ch.qos.logback.classic.AsyncAppender`, which overrides `isDiscardable`.

`KoraAsyncAppender` extends `AsyncAppenderBase` **directly** and does not override `isDiscardable`.
So no event is ever discarded by threshold, and setting `<discardingThreshold>0</discardingThreshold>`
— the standard advice for Logback's `AsyncAppender` — is a no-op you can drop from a ported
`logback.xml`. Back-pressure is governed by `neverBlock` alone.

## Caller data is unavailable

`KoraLoggingEvent` is a record whose caller-data accessors are hard-coded:

```java
@Override public StackTraceElement[] getCallerData() { return null; }
@Override public boolean hasCallerData()            { return false; }
```

Consequently `%class`, `%method`, `%line`, `%file` and `%caller` cannot be rendered for any event
that passed through `KoraAsyncAppender`, and `AsyncAppenderBase` declares no `includeCallerData`
property to turn it on. If you need caller data for a specific diagnostic, log through an appender
chain that bypasses `KoraAsyncAppender` — and accept that such records lose `traceId`/`spanId` and
Kora MDC.

## Serialisation happens on the appender thread

The event captures *references*: `getArgumentArray()`, `getMarkerList()`, `getKeyValuePairs()` and
the `koraMdc` writers are all invoked later, by the encoder, on the worker thread. The rendered
message string (`getFormattedMessage()`) is captured eagerly, but a `StructuredArgumentWriter`
lambda is not.

Pass immutable snapshots into structured arguments. A lambda that closes over a mutable builder or
an entity that the request goes on to modify will serialise whatever the object looks like when the
appender thread gets to it, not when the log call was made.

## Shutdown

`AsyncAppenderBase.stop()` drains the queue within `maxFlushTime`. Run the service through
`KoraApplication.run(...)` so the graph shuts down cleanly and Logback stops; a hard kill loses
whatever is still queued. If tail-end records are missing from a container's logs, raise
`maxFlushTime` before suspecting the appender.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `traceId` / `spanId` / Kora MDC missing | The appender chain does not include `KoraAsyncAppender`, or the encoder ignores `KoraLoggingEvent` |
| Logging threads block under load | Queue is full — raise `queueSize`, make the inner appender faster, or accept drops with `<neverBlock>true</neverBlock>` |
| `discardingThreshold` appears to be ignored | It is. `isDiscardable` always returns `false` for `AsyncAppenderBase` |
| `%class` / `%method` / `%line` render as `?` | Caller data is disabled at the event level, by design |
| Records lost at shutdown | Raise `maxFlushTime`; shut down through `KoraApplication.run(...)` |
| A logged object shows post-mutation state | The writer lambda ran on the appender thread — pass an immutable snapshot |
| High memory attributable to logging | Lower `queueSize`, or reduce event size (fewer/smaller structured fields) |
