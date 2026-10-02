# Cost and volume control for the logging aspect — Kora 2.0

Scoped to what `@Log` / `@Mdc` themselves cost and how to keep their output bounded. Appenders,
encoders and JSON output are the backend's concern —
see [kora-telemetry-logging](../../kora-telemetry-logging/SKILL.md).

## Contents

- [What the aspect actually costs](#what-the-aspect-actually-costs)
- [Level gates are the main lever](#level-gates-are-the-main-lever)
- [Per-method logger names](#per-method-logger-names)
- [Big payloads](#big-payloads)
- [Loops and batches](#loops-and-batches)
- [What `@Mdc` costs](#what-mdc-costs)
- [The error path](#the-error-path)

## What the aspect actually costs

The aspect is generated into a `$<Class>__AopProxy` subclass at build time: one virtual call, no
reflection, no runtime proxy, no `ThreadLocal` lookup. The `org.slf4j.Logger` is resolved once in the
proxy's constructor, not per call.

Everything expensive sits behind a level check. The generated code is shaped like:

```java
if (this.logger.isDebugEnabled()) {
    var __dataIn = StructuredArgument.marker("data", gen -> { … });
    this.logger.info(__dataIn, ">");
}
this.logger.info(">");   // when the payload level is disabled
```

so when the level is off you pay a boolean and the bare `>` / `<` write. When it is on you pay:

1. allocating the marker and its lambda,
2. rendering every logged argument — a bound `StructuredArgumentMapper<T>` if there is one, otherwise
   `String.valueOf(value)`, i.e. the value's `toString()`,
3. the appender write.

Item 2 is the one that hurts. A `toString()` over a large collection or an entity graph is invisible
at `INFO` and dominant at `DEBUG`.

## Level gates are the main lever

Entry/exit records are emitted at the annotation's level (default `INFO`); their payload is gated on
the more verbose of `DEBUG` and that level. So the default `@Log` gives:

| Logger level | Output |
|---|---|
| `WARN`+ | nothing (errors still log at `WARN`) |
| `INFO` | `>` and `<` only — no argument or result rendering at all |
| `DEBUG` / `TRACE` | `> {"data":{…}}` and `< {"data":{"out":…}}` |

That makes plain `@Log` safe to leave on a hot path in production: at `INFO` no argument is ever
converted to a string, so there is no PII exposure and no rendering cost. Turning a class up to
`DEBUG` is then a runtime decision, made in `logging.levels`:

```hocon
logging {
  levels {
    "ROOT": "WARN"
    "com.example": "INFO"
    "com.example.OrderService": "DEBUG"   # only this class renders arguments
  }
}
```

Remember that `LoggingLevelRefresher` clears per-logger levels from `logback.xml` at graph init, so
`logging.levels` is the only place these belong.

## Per-method logger names

The logger is `<class>.<method>`, so you can raise one method without raising its neighbours:

```hocon
logging.levels {
  "com.example.OrderService": "INFO"
  "com.example.OrderService.reconcile": "DEBUG"
}
```

## Big payloads

Keep the value out of the record rather than making it cheap to render:

```java
@Log
public Receipt upload(
    @Mdc(key = "fileName") String fileName,
    @Log.off byte[] data,                    // never rendered
    @Mdc(key = "fileSize") long size         // cheap context instead
) { ... }
```

`@Log.off` removes the argument before any mapper is consulted, so nothing is allocated for it at any
level. Where the object must be logged but only parts are sensitive, use `@Mask` — but note it still
serialises the whole object, so it is a confidentiality tool, not a volume tool. See
[logging-masking.md](logging-masking.md).

For a type you log often, a `@Mapping(...)` `StructuredArgumentMapper` that writes an id and a size
is far cheaper than the default `toString()` — see
[logging-aspect.md](logging-aspect.md#rendering-arguments-and-results).

## Loops and batches

`@Log` on a per-item method produces two records per item. Annotate the boundary instead:

```java
@Log
public void processBatch(List<Item> items) {   // one pair of records
    for (var item : items) {
        processItem(item);                     // no @Log* annotation at all
    }
}

private void processItem(Item item) { ... }
```

`@Log.off` on `processItem` would **not** silence it — it only suppresses the result payload. Silence
means no `@Log`, `@Log.in` or `@Log.out` on the method.

Note also that a private method called as `this.processItem(item)` never goes through the proxy, so
an annotation there would not fire regardless.

## What `@Mdc` costs

`MDC.put0` is copy-on-write: each put allocates a `HashMap` copy plus an immutable `Map.copyOf`. The
`@Mdc` aspect performs one put per key on entry and one restore per non-`global` key on exit, plus a
`values()` read up front. That is negligible per request and measurable in a tight loop — do not put
`@Mdc` on a method called thousands of times inside one request.

`global = true` skips the capture and the restore, but it is a correctness trade (the key stays
visible to the rest of the scope), not a performance technique.

## The error path

On any `Throwable` the aspect builds an `errorType` / `errorMessage` marker whenever `WARN` is
enabled, logs at `WARN`, and rethrows. The throwable itself is attached only when `DEBUG` is also
enabled — so a service running at `INFO` gets the error class and message without a stack trace on
every failure. If a method throws as part of normal control flow, that allocation happens on every
call; prefer not annotating such a method.

## See also

- [logging-aspect.md](logging-aspect.md) — `@Log` reference
- [logging-mdc.md](logging-mdc.md) — `@Mdc` reference
- [logging-masking.md](logging-masking.md) — `@Mask` reference
- Parent [SKILL.md](../SKILL.md)
