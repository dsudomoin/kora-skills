# Validation Annotations Reference

**Package:** `io.koraframework.validation.common.annotation` (artifact `io.koraframework:validation-common`)

Kora 2.0 ships **22 constraint annotations** plus the structural `@Valid`, `@Validate` and
`@ValidatedBy`. They are Kora's own — `jakarta.validation.*` / `javax.validation.*` are not
recognised and are silently ignored. There is **no `@NotNull`**: null checks are implicit
(see [Implicit nullability](#implicit-nullability-and-nullable)).

Every constraint shares the same shape:

```java
@AopAnnotation
@Documented
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.FIELD, ElementType.PARAMETER})
@ValidatedBy(SomeValidatorFactory.class)
public @interface SomeConstraint { ... }
```

`RetentionPolicy.CLASS` is deliberate: constraints are consumed by the annotation processor / KSP and
are not present at runtime.

## Contents

- [The complete constraint set](#the-complete-constraint-set)
- [Strings and character sequences](#strings-and-character-sequences)
- [Collections and maps](#collections-and-maps)
- [Numbers](#numbers)
- [Booleans](#booleans)
- [Temporal](#temporal)
- [Format constraints](#format-constraints)
- [@Valid](#valid)
- [@Validate](#validate)
- [@ValidatedBy](#validatedby)
- [Implicit nullability and @Nullable](#implicit-nullability-and-nullable)
- [Kotlin specifics](#kotlin-specifics)

---

## The complete constraint set

| Annotation | Attributes | Supported types |
|---|---|---|
| `@NotBlank` | — | `String`, `CharSequence` |
| `@NotEmpty` | — | `String`, `CharSequence`, `Iterable<T>`, `Collection<T>`, `List<T>`, `Set<T>`, `Map<K,V>` |
| `@Size` | `int min() default 0`, `int max()` | `String`, `CharSequence`, `Collection<V>`, `List<V>`, `Set<V>`, `Map<K,V>` |
| `@Pattern` | `String value()`, `int flags() default 0` | `String`, `CharSequence` |
| `@Range` | `double from()`, `double to()`, `Boundary boundary() default INCLUSIVE_INCLUSIVE` | `Short`, `Integer`, `Long`, `Float`, `Double`, `BigInteger`, `BigDecimal` |
| `@Min` | `long value()` | `Short`, `Integer`, `Long`, `Float`, `Double`, `BigInteger`, `BigDecimal` |
| `@Max` | `long value()` | same as `@Min` |
| `@Positive` | — | any `T extends Number` |
| `@PositiveOrZero` | — | any `T extends Number` |
| `@Negative` | — | any `T extends Number` |
| `@NegativeOrZero` | — | any `T extends Number` |
| `@Digits` | `int integer()`, `int fraction()` | `Short`, `Integer`, `Long`, `Float`, `Double`, `BigInteger`, `BigDecimal`, `String`, `CharSequence` |
| `@AssertTrue` | — | `Boolean` |
| `@AssertFalse` | — | `Boolean` |
| `@Past` | — | `LocalDate`, `LocalDateTime`, `Instant`, `OffsetDateTime`, `ZonedDateTime` |
| `@PastOrPresent` | — | same as `@Past` |
| `@Future` | — | same as `@Past` |
| `@FutureOrPresent` | — | same as `@Past` |
| `@Url` | — | `String`, `CharSequence` |
| `@Uri` | — | `String`, `CharSequence` |
| `@UUID` | — | `String`, `CharSequence` |
| `@OneOf` | `String[] value()` | `String`, `CharSequence` |

"Supported types" is the set of `*ValidatorFactory<T>` bindings that
`io.koraframework.validation.common.constraint.ValidatorModule` contributes. Applying a constraint to
a type outside that set gives a DI graph error naming the missing factory, e.g.
`NotBlankValidatorFactory<Integer>` — the fix is a different constraint, not a new factory.

Primitives are boxed before the factory is resolved, so `@Range(from = 1, to = 5) int qty` binds
`RangeValidatorFactory<Integer>`.

---

## Strings and character sequences

### `@NotBlank`

Not null, not empty, and not whitespace-only.

```java
@Valid
public record UserRequest(@NotBlank String name) {}   // "  " -> INVALID, "John" -> VALID
```

Messages: `Should be not blank, but was null` / `… but was empty` / `… but was blank`.

### `@NotEmpty`

Not null and length/size greater than zero. Whitespace-only strings pass.

```java
@Valid
public record OrderRequest(
    @NotEmpty String notes,        // "" -> INVALID, "  " -> VALID
    @NotEmpty List<String> items   // [] -> INVALID
) {}
```

Messages: `Should be not empty, but was null` / `… but was empty`.

### `@NotBlank` vs `@NotEmpty`

| | `String` / `CharSequence` | `Collection` / `Map` | `"   "` |
|---|---|---|---|
| `@NotBlank` | yes | no | rejected |
| `@NotEmpty` | yes | yes | accepted |

`@NotBlank` for human-typed strings; `@NotEmpty` for collections, or strings where whitespace is meaningful.

### `@Pattern`

```java
import io.koraframework.validation.common.annotation.Pattern;

@Valid
public record UserRequest(
    @Pattern("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$") String email,
    @Pattern(value = "^[a-z]{2}$", flags = java.util.regex.Pattern.CASE_INSENSITIVE) String country
) {}
```

- `value` is the unnamed attribute, so `@Pattern("...")` works. There is no `regexp` attribute.
- `flags` is a plain `int` — combine `java.util.regex.Pattern` constants with `|`. There is no
  `Pattern.Flag` enum.
- The check is `matcher.matches()` on the whole value. Message:
  `Should match RegEx <pattern> but was: <value>`.
- When the constraint and `java.util.regex.Pattern` are both in scope, import one and qualify the
  other — they share a simple name.

---

## Collections and maps

### `@Size`

Length (`CharSequence`) or `size()` (`Collection`, `Map`) within `[min, max]`, both inclusive.

```java
@Valid
public record UserRequest(
    @Size(min = 2, max = 100) String name,
    @Size(min = 1, max = 10) List<String> tags,
    @Size(max = 20) Map<String, String> attributes   // min defaults to 0
) {}
```

`max` is required and has no default; `min` defaults to `0`. `min < 0` or `max < min` throws
`IllegalArgumentException` while the validator is being constructed, i.e. during graph build.
Message: `Size should be in range from '<min>' to '<max>', but was greater: <n>`
(for `CharSequence` the prefix is `Length should be in range …`).

### Element validation

`ValidatorModule` derives `Validator<List<T>>`, `Validator<Set<T>>` and `Validator<Collection<T>>`
from `Validator<T>`, so marking the field `@Valid` validates every element:

```java
@Valid public record Order(@Valid List<OrderItem> items) {}
@Valid public record OrderItem(@NotBlank String sku) {}
// violation path for the first element -> "items.[0].sku"
```

A `null` collection reference is caught by the implicit null check; a non-null collection with a
`null` element produces a violation from the element validator.

---

## Numbers

### `@Range`

```java
@Valid
public record ProductRequest(
    @Range(from = 1, to = 1000) int quantity,
    @Range(from = 0.0, to = 100.0) Double discount,
    @Range(from = 0, to = 100, boundary = Range.Boundary.EXCLUSIVE_INCLUSIVE) Integer percent
) {}
```

| `Range.Boundary` | `from` | `to` | with `from=0, to=100` |
|---|---|---|---|
| `INCLUSIVE_INCLUSIVE` (default) | inclusive | inclusive | `0 <= x <= 100` |
| `INCLUSIVE_EXCLUSIVE` | inclusive | exclusive | `0 <= x < 100` |
| `EXCLUSIVE_INCLUSIVE` | exclusive | inclusive | `0 < x <= 100` |
| `EXCLUSIVE_EXCLUSIVE` | exclusive | exclusive | `0 < x < 100` |

`Boundary` is a nested enum on the annotation — reference it as `Range.Boundary.*`.

`from` and `to` are `double`, but the validator chosen depends on the **field** type:
integral types (`Short`/`Integer`/`Long`/`BigInteger`) use a long-based validator that **truncates**
the bounds, so `@Range(from = 0.5, to = 1.5) Integer` behaves as `0..1`. Use whole numbers on
integral fields. `to < from` throws `IllegalArgumentException` during graph build.

### `@Min` / `@Max`

Inclusive bounds, compared as `BigDecimal`, with a `long value()`:

```java
@Valid
public record Item(@Min(1) @Max(99) Integer count) {}
```

Use `@Min`/`@Max` for one-sided bounds and `@Range` when you need both plus boundary control.

### `@Positive` / `@PositiveOrZero` / `@Negative` / `@NegativeOrZero`

No attributes; comparison against zero as `BigDecimal`. Bound for any `T extends Number`.

### `@Digits`

```java
@Valid
public record Money(@Digits(integer = 10, fraction = 2) BigDecimal amount) {}
```

Counts digits after `stripTrailingZeros()`: `integer` caps the integral digits, `fraction` caps the
scale. Also accepts `String`/`CharSequence`, parsed via `new BigDecimal(value.toString())`; an
unparseable value yields `… but was invalid number: <value>`. Negative attributes throw
`IllegalArgumentException` during graph build.

---

## Booleans

`@AssertTrue` / `@AssertFalse`, bound only for `Boolean`:

```java
@Valid
public record Consent(@AssertTrue Boolean termsAccepted) {}
```

Message: `Should be true, but was: false`.

---

## Temporal

`@Past`, `@PastOrPresent`, `@Future`, `@FutureOrPresent`, bound for `LocalDate`, `LocalDateTime`,
`Instant`, `OffsetDateTime` and `ZonedDateTime`. "Now" is read at validation time from the matching
`now()` factory.

```java
@Valid
public record Booking(
    @Future LocalDate checkIn,
    @PastOrPresent Instant createdAt
) {}
```

`java.util.Date`, `Calendar` and `LocalTime` have no factory binding.

---

## Format constraints

| Annotation | Check |
|---|---|
| `@UUID` | `java.util.UUID.fromString(value)` succeeds |
| `@Uri` | `new java.net.URI(value)` succeeds |
| `@Url` | `new java.net.URI(value)` succeeds **and** both scheme and host are present |
| `@OneOf({"A","B"})` | `value.toString()` is in the given set |

```java
@Valid
public record Callback(
    @UUID String traceId,
    @Url String callbackUrl,
    @OneOf({"CREATED", "PAID", "CANCELLED"}) String status
) {}
```

`@OneOf` is the string-set constraint. `@Valid` on an `enum` is a compile error
(`Validation can't be generated for enum`) — validate the raw string with `@OneOf` before converting,
or let the JSON layer reject the unknown value.

---

## @Valid

**Target:** `TYPE`, `METHOD`, `FIELD`, `PARAMETER`. No attributes.

```java
// on a type -> generates $Address_Validator and registers Validator<Address> in the graph
@Valid
public record Address(@NotBlank String city, @NotBlank String street) {}

// on a field -> recurses into the nested type's Validator
@Valid
public record OrderRequest(@Valid Address shippingAddress) {}

// on a parameter -> validates the argument (needs @Validate on the method)
@Validate
public User create(@Valid CreateUserRequest request) { ... }

// on a method -> validates the returned object graph (needs @Validate too)
@Valid
@Validate
public User load(String id) { ... }
```

Accepted targets for generation: records, plain classes, `sealed` interfaces (each permitted subtype
needs its own `@Valid`), and `@ConfigSource` / `@ConfigMapper` interfaces. Enums and non-sealed
non-config interfaces are rejected with a compile error.

## @Validate

**Target:** `METHOD` only. **Attribute:** `boolean failFast() default false`.

```java
@Validate                       // collect every violation, then throw one ViolationException
public User create(@Valid CreateUserRequest request) { ... }

@Validate(failFast = true)      // throw on the first violation
public User getByEmail(@NotBlank String email) { ... }
```

The enclosing class must be proxyable — see the SKILL for the exact rules. A `final`/non-`open` class
or method is a compile error in both languages; only a Java abstract class or interface is skipped
silently.

## @ValidatedBy

**Target:** `ANNOTATION_TYPE`. **Attribute:** `Class<? extends ValidatorFactory> value()`.

Meta-annotation that binds a constraint annotation to the factory producing its `Validator<T>`. Every
built-in constraint carries one; it is also how custom constraints are declared —
see [custom-validators-reference.md](custom-validators-reference.md).

---

## Implicit nullability and `@Nullable`

Every field, argument and validated result is **implicitly required**: the generated validator emits
its own null check before running any constraint. There is no `@NotNull` annotation.

```java
@Valid
public record UserRequest(
    @NotBlank String name,                  // required
    @Nullable String middleName,            // optional — no null check, no generated code at all
    @Nullable List<@NotBlank String> tags
) {}
```

Generated messages: `Must be non null, but was null` for a field, `<Type> input must be non null, but
was null` for the root value, `Parameter '<name>' must be non null, but was null` for a `@Validate`
argument.

**Java:** use JSpecify — `org.jspecify.annotations.Nullable` (transitive with the Kora core
artifacts). It is a **type-use** annotation, so position matters:
`List<@Nullable String>` is not `@Nullable List<String>`. The processor accepts any annotation whose
fully-qualified name ends in `.Nullable`, but JSpecify is what Kora 2.0 itself is annotated with;
mixing flavours is how mismatched nullability contracts start.

**Kotlin:** nullability is the type — `String?`. Do not carry Java nullability annotations into
Kotlin.

A field that is `@Nullable` and carries no constraint produces **no generated code at all**, so
`@Nullable` is also the way to exclude a field from validation entirely.

---

## Kotlin specifics

```kotlin
@Valid
data class CreateUserRequest(
    @field:NotBlank @field:Size(min = 2, max = 100) val name: String,
    @field:Range(from = 18.0, to = 120.0) val age: Int,
    @field:OneOf("ACTIVE", "BLOCKED") val status: String,
    val note: String?
)
```

- **`@field:` is mandatory.** The constraints declare `METHOD`, `FIELD` and `PARAMETER` targets but
  not `PROPERTY`, so Kotlin's default use-site is the constructor parameter — where the class
  validator does not look. Without the prefix the data class compiles and is never validated.
- `@Range` attributes are `double`: `from = 18.0, to = 120.0`.
- `@Min`/`@Max` attributes are `long`: `@Min(1)` is fine, the literal widens.
- `@OneOf` takes a `vararg`-style array: `@field:OneOf("A", "B")`.

---

## See also

- [custom-validators-reference.md](custom-validators-reference.md) — custom constraints and factories
- [violation-exception-reference.md](violation-exception-reference.md) — `ViolationException`, paths, HTTP 400
