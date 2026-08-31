# `@Mask` — redacting fields inside logged values (Kora 2.0)

Annotation: **`io.koraframework.logging.common.annotation.Mask`**
Support types: **`io.koraframework.logging.common.masking`** — `MaskingStrategy`, `MaskingFull`,
`MaskingKeepFirst`, `MaskingKeepLast`, `MaskingRules`.
Artifact: `io.koraframework:logging-common`.

This is the mechanism for *partial* redaction — the object is still logged, but some of its JSON
fields are replaced. To drop a value from the record entirely, use `@Log.off` on the parameter
instead; see [logging-aspect.md](logging-aspect.md).

## Contents

- [Annotation shape](#annotation-shape)
- [The two roles of `@Mask`](#the-two-roles-of-mask)
- [Worked example](#worked-example)
- [Strategies](#strategies)
- [Generated rules](#generated-rules)
- [Rule matching](#rule-matching)
- [Structured vs stringified output](#structured-vs-stringified-output)
- [Custom rules with `@Mapping`](#custom-rules-with-mapping)
- [Java on 2.0.0.RC1: declare the rules yourself](#java-on-200rc1-declare-the-rules-yourself)
- [Pitfalls](#pitfalls)

## Annotation shape

```java
@Target({TYPE, PARAMETER, FIELD, RECORD_COMPONENT, METHOD, TYPE_USE})
@Retention(RUNTIME)
public @interface Mask {
    Class<? extends MaskingStrategy> value() default MaskingFull.class;
}
```

## The two roles of `@Mask`

`@Mask` does different work depending on where it sits, and a working setup needs both.

**1. On the logged element** — the `@Log` parameter or the `@Log`-annotated method. This switches the
aspect from `StructuredArgumentMapper<T>` to `MaskedStructuredArgumentMapper<T>`, which writes the
value through a `MaskingJsonGenerator`.

**2. On the type and its fields** — this is where the rules come from. The type must be a class or a
record annotated `@Json` (so a `JsonWriter<T>` exists) and `@Mask`; each field or record component
that must be redacted carries its own `@Mask`.

Both are required. `@Mask` on the parameter alone gives you a masked mapper with no rules; `@Mask` on
the type alone changes nothing about how the argument is logged.

## Worked example

```java
@Mask(MaskingKeepFirst.class)          // default strategy for masked fields of this type
@Json
public record Credentials(
    @Mask String secret,               // MaskingKeepFirst, inherited from the type
    @Mask(MaskingKeepLast.class) String token,
    String login                       // not masked
) {}
```

```java
@Mask
@Json
public record User(String name, Credentials credentials) {}
```

```java
@Component
public class AuthService {                       // non-final

    @Log.in
    public void authenticate(@Mask @Json User user) { ... }
}
```

Payload written on entry (verified in `LogAspectTest#testLogArgsWithMaskingMapper`, with
`MaskingKeepFirst("###", 2)` and `MaskingKeepLast("!!!", 3)` bound):

```json
{"arg1":{"name":"user","credentials":{"secret":"se###","token":"!!!ken","login":"login"}}}
```

Kotlin is the same shape:

```kotlin
@Mask(MaskingKeepLast::class)
@Json
data class Credentials(@Mask val secret: String, val login: String)

@Component
open class AuthService {
    @Log.`in`
    open fun authenticate(@Mask @Json user: User) { ... }
}
```

## Strategies

```java
public interface MaskingStrategy {
    String mask(Object value);
}
```

`mask` receives the scalar the JSON writer was about to emit (`String`, `Boolean`, `Number`,
`BigInteger`, `BigDecimal`, `byte[]`), or the source object for an object/array matched as a whole.
JSON `null` is never masked. Map **keys** are never masked as values.

| Built-in | Output for `"secret"` | Constructor |
|---|---|---|
| `MaskingFull` (default) | `***` | `()`, `(String replacement)` |
| `MaskingKeepFirst` | `secr***` | `()`, `(String replacement, int keep)` — defaults `***`, 4 |
| `MaskingKeepLast` | `***cret` | `()`, `(String replacement, int keep)` — defaults `***`, 4 |

All three are bound by `LoggingModule` as `@DefaultComponent`s built with their no-arg constructors.
Override the settings by declaring your own component of the same concrete type — `@DefaultComponent`
yields to a user-supplied one:

```java
@Module
public interface MaskingTuningModule {
    default MaskingKeepLast maskingKeepLast() { return new MaskingKeepLast("***", 2); }
}
```

A custom strategy is an ordinary component; the generated rules module takes it as a constructor
parameter, so it must be resolvable from the graph:

```java
@Component
public final class LastFourDigits implements MaskingStrategy {
    @Override public String mask(Object value) { return "**** " + value.toString().substring(12); }
}
```

```java
@Mask @Json
public record Card(@Mask(LastFourDigits.class) String pan) {}
```

## Generated rules

For every class or record annotated `@Mask`, the processor emits a `@Module` interface named
`$<Type>_MaskingRulesModule` with a `@DefaultComponent` factory:

```java
@Module
public interface $User_MaskingRulesModule {
    @DefaultComponent
    default MaskingRules<User> userMaskingRules(MaskingKeepFirst strategy0, MaskingKeepLast strategy1) {
        return MaskingRules.builder(User.class)
            .mask("credentials.secret", strategy0)
            .mask("credentials.token", strategy1)
            .build();
    }
}
```

`@Module` interfaces are discovered automatically across the compilation — do **not** add the
generated module to the `@KoraApp` `extends` clause.

How the processor walks the type:

- It descends only into field types that are themselves annotated `@Json`, `@JsonWriter` or `@Mask`.
- `@JsonField("name")` renaming and the type's naming strategy are honoured; `@JsonSkip` fields are
  ignored.
- Collections and arrays contribute **no** path segment; `Map` values contribute a `*` segment.
- Recursive types terminate (a type already on the current branch is not revisited).
- A field's strategy is: the field's own `@Mask(X)`, else the enclosing type's `@Mask(X)`, else
  `MaskingFull`.
- Only concrete classes and records qualify. An interface gives
  `Only classes and records can be annotated with @Mask`; an abstract class gives
  `Abstract classes can't be annotated with @Mask`.

## Rule matching

`MaskingRules.strategy(path, fieldName)` checks path rules first, then field rules:

| Rule string | Matches |
|---|---|
| `password` | any JSON field named `password`, at any depth |
| `user.password` | `password` reached through `user`, counted from the logged root |
| `users.*.password` | one dynamic segment — the value objects of a `Map` |

Build them by hand with the public API:

```java
MaskingRules<User> rules = MaskingRules.builder(User.class)
    .mask("password", new MaskingFull())
    .mask("credentials.token", new MaskingKeepLast())
    .build();
```

or with the map constructor: `new MaskingRules<>(User.class, Map.of("token", strategy))`.

## Structured vs stringified output

`MaskedStructuredArgumentMapper<T>` has a `structured` flag, and the aspect sets it from whether the
logged element also carries `@Json`:

| On the parameter / method | Payload |
|---|---|
| `@Mask @Json` | nested JSON object: `{"arg1":{"name":"user","token":"***"}}` |
| `@Mask` only | the masked JSON as one escaped string: `{"arg1":"{\"name\":\"user\",\"token\":\"***\"}"}` |

Prefer `@Mask @Json` — a nested object is what a log aggregator can index.

## Custom rules with `@Mapping`

Point a single logged element at your own rules by subclassing `MaskingRules<T>` and selecting it
with `@Mapping` (`io.koraframework.common.annotation.Mapping`):

```java
public final class CustomRules extends MaskingRules<User> {
    public CustomRules() {
        super(User.class, Map.of("token", value -> "rules-" + value));
    }
}
```

```java
@Log.in
public void handle(@Mask @Json @Mapping(CustomRules.class) User user) { ... }
```

The aspect then constructs `new MaskedStructuredArgumentMapper<>(jsonWriter, customRules, hasJson)`
directly, so `CustomRules` and a `JsonWriter<User>` must both be resolvable from the graph.

## Java on 2.0.0.RC1: declare the rules yourself

Verified against the published binaries:

- `logging-symbol-processor-2.0.0.RC1.jar` registers
  `META-INF/services/com.google.devtools.ksp.processing.SymbolProcessorProvider` →
  `MaskingRulesSymbolProcessorProvider`. **Kotlin generates `$<Type>_MaskingRulesModule`
  automatically.**
- `logging-annotation-processor-2.0.0.RC1.jar` contains `LoggingAnnotationProcessor` and
  `MaskingRulesProcessor` classes but registers **only**
  `META-INF/services/io.koraframework.aop.annotation.processor.KoraAspectFactory`. There is no
  `javax.annotation.processing.Processor` entry, so javac never runs it. **In Java no masking rules
  module is generated.**

`@Mask` on the parameter still works — the aspect asks the graph for
`MaskedStructuredArgumentMapper<User>`, which `LoggingModule` builds from a `JsonWriter<User>` plus a
`MaskingRules<User>`. Only the second one is missing, and it is not an optional dependency of that
factory, so the graph build fails on an unresolved `MaskingRules<User>`.

Supply it yourself — the same component the generated module would have declared:

```java
import io.koraframework.common.annotation.Module;
import io.koraframework.logging.common.masking.MaskingFull;
import io.koraframework.logging.common.masking.MaskingKeepLast;
import io.koraframework.logging.common.masking.MaskingRules;

@Module
public interface UserMaskingModule {
    default MaskingRules<User> userMaskingRules() {
        return MaskingRules.builder(User.class)
            .mask("credentials.secret", new MaskingFull())
            .mask("credentials.token", new MaskingKeepLast())
            .build();
    }
}
```

Field-level `@Mask` annotations are then documentation only — the hand-written builder is the source
of truth, so keep the two in step. Kotlin services need none of this.

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Graph build fails on `MaskingRules<Foo>` in a Java service | RC1 does not register the Java masking processor | Declare the `MaskingRules<Foo>` component by hand (above) |
| Graph build fails on `JsonWriter<Foo>` | The logged type is not `@Json`, or `JsonModule` is not in the `@KoraApp` | Add `@Json` to the type and `io.koraframework.json.common.JsonModule` to the app |
| The value logs as one escaped JSON string | `@Mask` without `@Json` on the logged element | Add `@Json` next to `@Mask` |
| A nested field is not masked | The nested type is not annotated `@Json`/`@Mask`, so the walker did not descend into it | Annotate the nested type |
| A renamed field is not masked | Rules use the **JSON** name | Match `@JsonField("…")`, not the Java field name |
| `Only classes and records can be annotated with @Mask` | `@Mask` on an interface or enum | Move it to the concrete type |
| `Abstract classes can't be annotated with @Mask` | `@Mask` on an abstract class | Move it to the concrete subtype |
| Custom `MaskingStrategy` not found | It is not a component | Add `@Component` or declare it in a `@Module` |
| Secret must not be logged at all | `@Mask` still emits a redacted placeholder | Use `@Log.off` on the parameter |

## See also

- [logging-aspect.md](logging-aspect.md) — `@Log` family and argument mappers
- [logging-mdc.md](logging-mdc.md) — `@Mdc` and the MDC runtime model
- [logging-performance.md](logging-performance.md) — cost model and volume control
- Parent [SKILL.md](../SKILL.md)
