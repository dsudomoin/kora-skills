# Custom Validators Reference

Building a constraint annotation of your own when the 22 built-ins are not enough.

**Packages:**
- `Validator`, `ValidatorFactory`, `ValidationContext`, `Violation` — `io.koraframework.validation.common`
- `ValidatedBy`, `Valid`, `Validate` — `io.koraframework.validation.common.annotation`

## Contents

- [The five steps](#the-five-steps)
- [Worked example: a plain constraint](#worked-example-a-plain-constraint)
- [Constraints with attributes](#constraints-with-attributes)
- [Validating a value type](#validating-a-value-type)
- [The contracts](#the-contracts)
- [Building violations](#building-violations)
- [Using the annotation](#using-the-annotation)
- [Reusing a built-in factory contract](#reusing-a-built-in-factory-contract)
- [Testing](#testing)
- [Common errors](#common-errors)

---

## The five steps

1. Implement `Validator<T>` for the type the annotation will sit on.
2. Declare a named `ValidatorFactory<T>` sub-interface. If the annotation has attributes, give it a
   `create(...)` overload with **one parameter per attribute, in declaration order**.
3. Register that factory as a graph component — a `default` method on the `@KoraApp` interface, or a
   `@Component` class.
4. Declare the annotation with `@Retention(RetentionPolicy.CLASS)`, a `@Target`, and
   `@ValidatedBy(YourFactory.class)`.
5. Put the annotation on a field / parameter / method inside a `@Valid` or `@Validate` context.

The generated validator receives the **factory** through DI and calls `create(...)` once in its
constructor, so the `Validator<T>` instance itself is not a graph component and does not need to be
thread-safe beyond being stateless.

---

## Worked example: a plain constraint

### 1. Validator

`T` is the type the annotation sits on. For a constraint on a `String` field, implement
`Validator<String>`.

```java
package com.example.app.validation;

import org.jspecify.annotations.Nullable;
import io.koraframework.validation.common.ValidationContext;
import io.koraframework.validation.common.Validator;
import io.koraframework.validation.common.Violation;

import java.util.List;

final class NotEmptyTrimmedValidator implements Validator<String> {

    @Override
    public List<Violation> validate(@Nullable String value, ValidationContext context) {
        if (value == null) {
            return List.of(context.violates("Should be not empty, but was null"));
        }
        if (value.strip().isEmpty()) {
            return List.of(context.violates("Should be not empty after trimming, but was: '" + value + "'"));
        }
        return List.of();
    }
}
```

Return an empty list when the value is valid. `validate` is called with the value as-is, so it must
handle `null` itself even though the generated validator usually null-checks first — a `@Nullable`
field is skipped entirely, but a `@Validate` result path can still reach it.

### 2. Factory

```java
package com.example.app.validation;

import io.koraframework.validation.common.ValidatorFactory;

public interface NotEmptyTrimmedValidatorFactory extends ValidatorFactory<String> { }
```

`ValidatorFactory<T>` is a `@FunctionalInterface` with a single `Validator<T> create()`, so a
constructor reference satisfies it.

### 3. Register the factory

```java
@KoraApp
public interface Application extends ValidatorModule {

    default NotEmptyTrimmedValidatorFactory notEmptyTrimmedValidatorFactory() {
        return NotEmptyTrimmedValidator::new;              // satisfies create()
    }
}
```

The module must still extend `ValidatorModule` (or `ValidationModule`) so the **built-in** factories
remain available.

### 4. Annotation

```java
package com.example.app.validation;

import io.koraframework.common.annotation.AopAnnotation;
import io.koraframework.validation.common.annotation.ValidatedBy;

import java.lang.annotation.*;

@AopAnnotation
@Documented
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.FIELD, ElementType.PARAMETER})
@ValidatedBy(NotEmptyTrimmedValidatorFactory.class)
public @interface NotEmptyTrimmed { }
```

- `@Retention(RetentionPolicy.CLASS)` — constraints are compile-time only.
- `@Target` must include every position you intend to use. `FIELD` is what a plain class and a Kotlin
  `@field:` annotation need; `PARAMETER` is for `@Validate` arguments; `METHOD` is for results.
- `@AopAnnotation` (from `io.koraframework.common.annotation`) matches what every built-in constraint
  declares. It is not required for the constraint to be picked up.

### 5. Use it

```java
@Valid
public record Foo(@NotEmptyTrimmed String number) { }
```

`@Valid` on `Foo` makes Kora generate `$Foo_Validator`, whose constructor takes
`NotEmptyTrimmedValidatorFactory` and calls `create()`.

---

## Constraints with attributes

Annotation attributes **are** passed to the factory. Declare a `create(...)` with exactly as many
parameters as the annotation has attributes, in the annotation's declaration order; the processor
resolves values with defaults applied and emits a direct call.

```java
public interface PrefixedValidatorFactory<T> extends ValidatorFactory<T> {

    @Override
    default Validator<T> create() {
        throw new UnsupportedOperationException("Prefixed requires a prefix; call create(String, int)");
    }

    Validator<T> create(String prefix, int minLength);     // one parameter per attribute
}
```

```java
@AopAnnotation
@Documented
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.FIELD, ElementType.PARAMETER})
@ValidatedBy(PrefixedValidatorFactory.class)
public @interface Prefixed {
    String prefix();
    int minLength() default 3;                             // declaration order: prefix, minLength
}
```

```java
public final class PrefixedValidator implements Validator<String> {
    private final String prefix;
    private final int minLength;

    public PrefixedValidator(String prefix, int minLength) {
        this.prefix = prefix;
        this.minLength = minLength;
    }

    @Override
    public List<Violation> validate(@Nullable String value, ValidationContext context) {
        if (value == null) return List.of(context.violates("Should start with '" + prefix + "', but was null"));
        if (!value.startsWith(prefix)) return List.of(context.violates("Should start with '" + prefix + "', but was: " + value));
        if (value.length() < minLength) return List.of(context.violates("Should be at least " + minLength + " chars, but was: " + value.length()));
        return List.of();
    }
}
```

```java
@KoraApp
public interface Application extends ValidatorModule {
    default PrefixedValidatorFactory<String> prefixedStringValidatorFactory() {
        return PrefixedValidator::new;                     // matches create(String, int)
    }
}
```

With `@Prefixed(prefix = "ME")` on a field, the generated validator constructor calls
`create("ME", 3)` — the default for `minLength` is filled in at compile time.

**Arity is checked at compile time.** A mismatch fails the build on the factory interface:

```text
error: Expected PrefixedValidatorFactory#create() method with 2 parameters, but was didn't find such
```

Nested enum types declared inside the annotation are not attributes and are not counted — that is why
`@Range` has three attributes (`from`, `to`, `boundary`) even though `Boundary` is declared alongside them.

---

## Validating a value type

Same shape, with the wrapper as `T`:

```java
public record Iban(String value) {}

final class IbanValidator implements Validator<Iban> {
    @Override
    public List<Violation> validate(@Nullable Iban value, ValidationContext context) {
        if (value == null) {
            return List.of(context.violates("IBAN must not be null"));
        }
        if (!isValidIban(value.value())) {
            return List.of(context.violates("Invalid IBAN format"));
        }
        return List.of();
    }

    private boolean isValidIban(String iban) { /* mod-97 checksum */ return true; }
}

public interface IbanValidatorFactory extends ValidatorFactory<Iban> { }

@KoraApp
public interface Application extends ValidatorModule {
    default IbanValidatorFactory ibanValidatorFactory() { return IbanValidator::new; }
}

@AopAnnotation
@Retention(RetentionPolicy.CLASS)
@Target({ElementType.METHOD, ElementType.FIELD, ElementType.PARAMETER})
@ValidatedBy(IbanValidatorFactory.class)
public @interface ValidIban {}
```

A generic factory (`interface XFactory<T> extends ValidatorFactory<T>`) is parameterised by the
annotated field's type, so one factory interface can serve `String`, `CharSequence` and so on with a
module method per binding. A non-generic factory (`extends ValidatorFactory<Iban>`) is used as-is.

---

## The contracts

```java
package io.koraframework.validation.common;

public interface Validator<T> {
    List<Violation> validate(@Nullable T value, ValidationContext context);

    default List<Violation> validate(@Nullable T value);
    default void validateAndThrow(@Nullable T value, ValidationContext context) throws ViolationException;
    default void validateAndThrow(@Nullable T value) throws ViolationException;
}

@FunctionalInterface
public interface ValidatorFactory<T> {
    Validator<T> create();
}
```

The contracts are `@NullMarked` (JSpecify), so a **Kotlin** implementation must match the nullability
exactly:

```kotlin
class IbanValidator : Validator<Iban> {
    override fun validate(value: Iban?, context: ValidationContext): List<Violation> { ... }
}
```

With `value: Iban` Kotlin reports `'validate' overrides nothing` and says nothing about nullability.

---

## Building violations

`ValidationContext` carries the current path and the fail-fast flag:

```java
context.violates("Field is invalid");                       // violation at the current path
context.addPath("nested").addPath("field").violates("...");  // nested path -> "nested.field"
context.addPath(0).violates("...");                          // collection element -> "[0]"
context.isFailFast();                                        // honour it in multi-check validators
```

A custom validator that performs several independent checks should honour `isFailFast()` the way the
built-ins do: return the first violation immediately when it is set, otherwise accumulate.

Standalone contexts: `ValidationContext.full()`, `ValidationContext.failFast()`, or
`ValidationContext.builder().failFast(true).build()`.

---

## Using the annotation

On DTO fields:

```java
@Valid
public record PaymentRequest(
    @ValidIban Iban iban,
    @NotBlank String accountId
) {}
```

On method parameters (with `@Validate` on the method):

```java
@Validate
public Payment processPayment(@ValidIban Iban beneficiaryIban) { ... }
```

On a method result:

```java
@ValidIban
@Validate
public Iban generateIban(String accountId) { ... }
```

In Kotlin, remember the use-site target on properties: `@field:ValidIban val iban: Iban`.

---

## Reusing a built-in factory contract

The built-in factory contracts in `io.koraframework.validation.common.constraint.factory` are public
interfaces. To extend a built-in constraint to a type Kora does not bind out of the box, add a module
method for that binding instead of writing a new annotation:

```java
@KoraApp
public interface Application extends ValidatorModule {

    // @NotBlank on a domain wrapper type
    default NotBlankValidatorFactory<AccountId> notBlankAccountIdValidatorFactory() {
        return () -> (value, context) -> (value == null || value.raw().isBlank())
            ? List.of(context.violates("Should be not blank"))
            : List.of();
    }
}
```

A binding for a type Kora does not cover is simply a new method — nothing to override. To replace one
of Kora's own bindings (say `NotBlankValidatorFactory<String>`), `@Override` the `ValidatorModule`
method: the built-ins are declared `@DefaultComponent`, so the application's version wins without an
"ambiguous dependency" error.

---

## Testing

```java
@KoraAppTest(Application.class)
class IbanValidationTest {

    @TestComponent
    private PaymentService service;        // the @Validate proxy exercises the constraint

    @Test
    void rejectsBadIban() {
        var ex = assertThrows(ViolationException.class,
            () -> service.processPayment(new Iban("nope")));

        assertTrue(ex.getViolations().stream().anyMatch(v -> v.message().contains("Invalid IBAN")));
    }
}
```

The validator itself is a plain object, so the cheapest test needs no graph at all:

```java
@Test
void rejectsNull() {
    var violations = new IbanValidator().validate(null, ValidationContext.full());
    assertEquals(1, violations.size());
}
```

---

## Common errors

| Message / symptom | Cause | Fix |
|---|---|---|
| `Expected X#create() method with N parameters, but was didn't find such` | annotation attribute count does not match any `create(...)` | Add the overload; count attributes, not nested types |
| `No component found for dependency: …ValidatorFactory<T> (no tags)` | the factory is not registered, or registered for a different `T` | Add the module method for exactly that `T` |
| Constraint has no effect at all | `@Retention(RUNTIME)`, missing `@ValidatedBy`, or a `@Target` that excludes the position used | Use `RetentionPolicy.CLASS`, `@ValidatedBy`, and the right targets |
| Constraint ignored on a Kotlin property | missing `@field:` use-site target | `@field:ValidIban` |
| Kotlin `'validate' overrides nothing` | parameter declared non-nullable | `value: T?` — the contract is `@NullMarked` with a `@Nullable` value |

---

## See also

- [validation-annotations-reference.md](validation-annotations-reference.md) — the 22 built-in constraints
- [violation-exception-reference.md](violation-exception-reference.md) — `ViolationException` handling
