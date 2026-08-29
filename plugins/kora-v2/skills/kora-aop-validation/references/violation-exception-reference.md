# ViolationException Reference

Handling validation failures and turning them into HTTP responses.

**Packages:**
- `ViolationException`, `Violation`, `ValidationContext`, `Validator` — `io.koraframework.validation.common` (artifact `validation-common`)
- `ValidationHttpServerInterceptor`, `ViolationExceptionHttpServerResponseMapper` — `io.koraframework.validation.module.http.server` (artifact `validation-module`)
- `ValidationModule` — `io.koraframework.validation.module`

## Contents

- [The types](#the-types)
- [When ViolationException is thrown](#when-violationexception-is-thrown)
- [HTTP 400 — how it actually works](#http-400--how-it-actually-works)
- [Wiring it in Java](#wiring-it-in-java)
- [Wiring it in Kotlin](#wiring-it-in-kotlin)
- [OpenAPI-generated servers](#openapi-generated-servers)
- [Violation paths](#violation-paths)
- [Inspecting violations](#inspecting-violations)
- [Fail-fast vs collect-all](#fail-fast-vs-collect-all)
- [Testing](#testing)
- [Common pitfalls](#common-pitfalls)

---

## The types

```java
package io.koraframework.validation.common;

public final class ViolationException extends RuntimeException {
    public ViolationException(Violation violation);
    public ViolationException(List<Violation> violations);
    public List<Violation> getViolations();
    // getMessage() renders "Validation failed with N violations:\n1) Path '<path>' violation: <message>"
}

public interface Violation {
    String message();                 // human-readable failure message
    ValidationContext.Path path();    // location of the failure
}
```

`ViolationException` is unchecked and is constructed lazily — `getMessage()` builds the rendered text
on first call from `getViolations()`.

`ValidationContext` is the validation-scoped path and fail-fast state. It is **not** the Kora 1.x
`Context` propagation type, which does not exist anywhere in Kora 2.0.

```java
public interface ValidationContext {
    Path path();
    boolean isFailFast();
    ValidationContext addPath(String path);
    ValidationContext addPath(int pathIndex);
    Violation violates(String message);

    static Builder builder();          // builder().failFast(boolean).build()
    static ValidationContext full();       // collect all
    static ValidationContext failFast();   // stop at the first

    interface Path {
        String value();   // this segment — a field name, or "[n]" for an index
        Path root();      // parent path, null at the root
        Path add(String field);
        Path add(int index);
        String full();    // dotted full path, e.g. "customer.address.city"
        static Path of(String path);
    }
}
```

There is no `ViolationPath` type and no `fieldName()` / `parent()` / `elements()` methods — use
`path().full()` and `path().value()`.

---

## When `ViolationException` is thrown

### From a `@Validate` method

```java
@Component
public class UserService {                                  // NOT final

    @Validate
    public User create(@Valid CreateUserRequest request) {
        return repository.save(request);   // throws BEFORE the body when the request is invalid
    }
}
```

Result violations are thrown after the body has produced the value.

### From manual validation

```java
@Component
public class OrderService {

    private final Validator<OrderRequest> validator;

    public OrderService(Validator<OrderRequest> validator) {
        this.validator = validator;
    }

    public Order create(OrderRequest request) {
        validator.validateAndThrow(request);                       // collect all, then throw
        validator.validateAndThrow(request, ValidationContext.failFast());   // or stop at the first
        return repository.save(request);
    }
}
```

### From configuration

`@Valid` on a `@ConfigSource` / `@ConfigMapper` interface makes the generated config mapper call
`validateAndThrow` on the parsed value, so an invalid `application.conf` fails during graph build with
a `ViolationException`.

---

## HTTP 400 — how it actually works

Two collaborating types, both in `validation-module`:

| Type | Kind | Role |
|---|---|---|
| `ValidationHttpServerInterceptor` | class, implements `HttpServerInterceptor` | Wraps the chain, catches `ViolationException`, produces the response |
| `ViolationExceptionHttpServerResponseMapper` | interface, `@Nullable HttpServerResponse apply(HttpServerRequest, ViolationException)` | Optional collaborator that shapes the body |

```java
public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
    try {
        return chain.process(request);
    } catch (ViolationException e) {
        return toResponse(request, e);       // mapper first; else HttpServerResponseException.of(400, message)
    }
}
```

The mapper is **not** an `HttpServerResponseMapper` and is not discovered by the response-mapper
machinery — it is a plain component the interceptor holds. If it is absent, or returns `null`, the
interceptor falls back to a plain-text `400` carrying `exception.getMessage()`.

**`ValidationModule` alone does not wire the interceptor into the server.** It declares:

```java
@DefaultComponent
default ValidationHttpServerInterceptor validationHttpServerInterceptor(
        @Nullable ViolationExceptionHttpServerResponseMapper mapper) {
    return new ValidationHttpServerInterceptor(mapper);
}
```

with **no tag**, while the router collects `@Tag(HttpServer.class) All<HttpServerInterceptor>`. An
untagged interceptor is never invoked (and, with nothing depending on it, is pruned from the graph
entirely). Tagging it is an explicit application step.

---

## Wiring it in Java

```java
package com.example.app;

import java.util.List;
import io.koraframework.application.graph.KoraApplication;
import io.koraframework.common.annotation.KoraApp;
import io.koraframework.common.annotation.Tag;
import io.koraframework.config.hocon.HoconConfigModule;
import io.koraframework.http.common.body.HttpBody;
import io.koraframework.http.server.common.HttpServer;
import io.koraframework.http.server.common.response.HttpServerResponse;
import io.koraframework.http.server.undertow.UndertowPublicHttpServerModule;
import io.koraframework.json.common.JsonModule;
import io.koraframework.json.common.JsonWriter;
import io.koraframework.logging.logback.LogbackModule;
import io.koraframework.validation.common.Violation;
import io.koraframework.validation.module.ValidationModule;
import io.koraframework.validation.module.http.server.ValidationHttpServerInterceptor;
import io.koraframework.validation.module.http.server.ViolationExceptionHttpServerResponseMapper;

@KoraApp
public interface Application extends
        HoconConfigModule,
        JsonModule,
        LogbackModule,
        ValidationModule,
        UndertowPublicHttpServerModule {

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }

    default ViolationExceptionHttpServerResponseMapper violationExceptionHttpServerResponseMapper(
            JsonWriter<ValidationErrorResponse> errorResponseJsonWriter) {
        return (request, exception) -> HttpServerResponse.of(
            400,
            HttpBody.json(errorResponseJsonWriter.toByteArray(
                ValidationErrorResponse.of(toValidationErrors(exception.getViolations())))));
    }

    @Tag(HttpServer.class)
    default ValidationHttpServerInterceptor validationHttpServerInterceptor(
            ViolationExceptionHttpServerResponseMapper violationExceptionHttpServerResponseMapper) {
        return new ValidationHttpServerInterceptor(violationExceptionHttpServerResponseMapper);
    }

    private static List<ValidationErrorDetails> toValidationErrors(List<Violation> violations) {
        return violations.stream()
            .map(violation -> new ValidationErrorDetails(normalizeField(violation), violation.message()))
            .toList();
    }

    private static String normalizeField(Violation violation) {
        String fullPath = violation.path().full();
        int lastDot = fullPath.lastIndexOf('.');
        return lastDot >= 0 ? fullPath.substring(lastDot + 1) : fullPath;
    }
}
```

Notes:

- `@Tag(HttpServer.class)` — `io.koraframework.http.server.common.HttpServer`. The Kora 1.x
  `@Tag(HttpServerModule.class)` still compiles and the interceptor silently never runs.
- `JsonWriter.toByteArray(...)` — the Kora 1.x `toByteArrayUnchecked` / `toStringUnchecked` methods do
  not exist in 2.0, and the plain ones no longer declare checked exceptions, so a `try/catch
  (IOException)` around them is a compile error.
- Redeclaring the module method here **narrows** it: the parameter is no longer `@Nullable`, so the
  mapper becomes a hard dependency. That is what you want — a missing mapper then fails the graph
  build instead of silently degrading to plain-text 400s.

### Error response DTOs

```java
@Json
public record ValidationErrorDetails(String field, String message) {}

@Json
public record ValidationErrorResponse(String code, String message, List<ValidationErrorDetails> errors) {
    public static ValidationErrorResponse of(List<ValidationErrorDetails> errors) {
        return new ValidationErrorResponse("VALIDATION_ERROR", "Validation failed", errors);
    }
}
```

### Resulting response

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "code": "VALIDATION_ERROR",
  "message": "Validation failed",
  "errors": [
    { "field": "email", "message": "Should match RegEx ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$ but was: nope" },
    { "field": "name",  "message": "Length should be in range from '2' to '100', but was smaller: 1" }
  ]
}
```

---

## Wiring it in Kotlin

```kotlin
@KoraApp
interface Application :
    HoconConfigModule,
    JsonModule,
    LogbackModule,
    ValidationModule,
    UndertowPublicHttpServerModule {

    fun violationExceptionHttpServerResponseMapper(
        errorResponseJsonWriter: JsonWriter<ValidationErrorResponse>
    ): ViolationExceptionHttpServerResponseMapper =
        ViolationExceptionHttpServerResponseMapper { _, exception ->
            HttpServerResponse.of(
                400,
                HttpBody.json(
                    errorResponseJsonWriter.toByteArray(
                        ValidationErrorResponse.of(toValidationErrors(exception.violations))
                    )
                )
            )
        }

    @Tag(HttpServer::class)
    override fun validationHttpServerInterceptor(
        violationExceptionHttpServerResponseMapper: ViolationExceptionHttpServerResponseMapper?
    ): ValidationHttpServerInterceptor =
        ValidationHttpServerInterceptor(violationExceptionHttpServerResponseMapper)

    private fun toValidationErrors(violations: List<Violation>): List<ValidationErrorDetails> =
        violations.map { ValidationErrorDetails(normalizeField(it), it.message()) }

    private fun normalizeField(violation: Violation): String {
        val fullPath = violation.path().full()
        val lastDot = fullPath.lastIndexOf('.')
        return if (lastDot >= 0) fullPath.substring(lastDot + 1) else fullPath
    }
}

fun main() {
    KoraApplication.run(ApplicationGraph::graph)
}
```

Kotlin differences that bite:

- The parameter **must** be `ViolationExceptionHttpServerResponseMapper?`. The Java module declares it
  `@Nullable` under JSpecify; with a non-null type Kotlin reports
  `'validationHttpServerInterceptor' overrides nothing` and never mentions nullability.
- `override` is required because the signature matches the inherited `ValidationModule` method.
  The mapper provider is a new method, so no `override` there.
- `exception.violations` is the Kotlin property view of `getViolations()`.

---

## OpenAPI-generated servers

The Kora OpenAPI generator wires the interceptor per-controller rather than globally. With
`enableServerValidation=true` it also emits `@InterceptWith(ValidationHttpServerInterceptor.class)` on
every generated controller, controlled by `enableServerValidationInterceptor` (default `true`):

```groovy
configOptions = [
    mode                            : "java-server",
    enableServerValidation          : "true",
    enableServerValidationInterceptor: "false"   // when mapping validation errors by hand
]
```

Leave the interceptor generation on for the default plain-text 400, or turn it off and wire your own
`@Tag(HttpServer.class)` interceptor plus mapper as above. See
[`kora-openapi-generator-server`](../../kora-openapi-generator-server/SKILL.md).

---

## Violation paths

```java
@Valid public record UserRequest(@NotBlank String name) {}
// path().full() -> "name"

@Valid public record OrderRequest(@Valid Customer customer) {}
@Valid public record Customer(@Valid Address address) {}
@Valid public record Address(@NotBlank String city) {}
// path().full() -> "customer.address.city"

@Valid public record Order(@Valid List<OrderItem> items) {}
@Valid public record OrderItem(@NotBlank String productId) {}
// first element -> "items.[0].productId"   (index segments render as [n])
```

For `@Validate` arguments the root segment is the parameter name (`user`, `code`), and the null
message is `Parameter '<name>' must be non null, but was null`. For a validated result the root
segment is empty and the message is `Result must be non null, but was null`.

---

## Inspecting violations

```java
try {
    validator.validateAndThrow(request);
} catch (ViolationException ex) {
    List<Violation> violations = ex.getViolations();

    boolean hasNameError = violations.stream()
        .anyMatch(v -> v.path().full().contains("name"));

    Map<String, String> errorMap = violations.stream()
        .collect(Collectors.toMap(v -> v.path().full(), Violation::message, (a, b) -> a));
}
```

`toMap` needs a merge function — two constraints on one field produce two violations with the same
path.

---

## Fail-fast vs collect-all

| Mode | How | Behaviour |
|---|---|---|
| Collect-all (default) | `@Validate` / `validate(value)` / `ValidationContext.full()` | Gathers every violation, throws once |
| Fail-fast | `@Validate(failFast = true)` / `ValidationContext.failFast()` / `ValidationContext.builder().failFast(true).build()` | Returns/throws on the first violation |

```java
List<Violation> violations = validator.validate(request, ValidationContext.failFast());
```

Fail-fast is carried in the context, so it propagates into nested validators and collection elements.

---

## Testing

```java
@KoraAppTest(Application.class)
class CreateUserValidationTest {

    @TestComponent
    private UserService service;          // the @Validate proxy

    @Test
    void failsForBlankName() {
        var request = new CreateUserRequest("   ", "test@example.com", 25, null);

        var ex = assertThrows(ViolationException.class, () -> service.create(request));

        assertTrue(ex.getViolations().stream().anyMatch(v -> v.path().full().contains("name")));
    }

    @Test
    void collectsAllViolations() {
        var request = new CreateUserRequest("   ", "invalid", 5, null);

        var ex = assertThrows(ViolationException.class, () -> service.create(request));

        assertEquals(3, ex.getViolations().size());   // name, email, age
    }
}
```

`@KoraAppTest` and `@TestComponent` are in `io.koraframework.test.extension.junit5`
(artifact `io.koraframework:test-junit5`). `@TestComponent` resolves nodes from the already-built
graph, so injecting `Validator<CreateUserRequest>` directly only works when the application already
depends on it; going through the `@Validate` component always works.

---

## Common pitfalls

| Problem | Cause | Fix |
|---|---|---|
| 500 instead of 400 | interceptor untagged, or tagged `@Tag(HttpServerModule.class)` | Override the module method with `@Tag(HttpServer.class)` |
| 400 with a plain-text body instead of JSON | no `ViolationExceptionHttpServerResponseMapper` in the graph, or it returned `null` | Provide the mapper and return a non-null response |
| Kotlin `'validationHttpServerInterceptor' overrides nothing` | parameter declared non-nullable | `ViolationExceptionHttpServerResponseMapper?` |
| `toByteArrayUnchecked` does not resolve | Kora 1.x JSON API | `JsonWriter.toByteArray(...)` |
| `exception IOException is never thrown` | `try/catch (IOException)` left around `toByteArray` | Remove the catch — 2.0 does not declare it |
| Wrong exception type caught | `jakarta.validation.ConstraintViolationException` | Catch `io.koraframework.validation.common.ViolationException` |
| `path().fieldName()` does not compile | no such method | `path().full()` / `path().value()` |
| Empty violations list | the exception did not come from Kora validation | Check that `@Validate` / the validator actually ran (a Java abstract class or interface is skipped without a diagnostic — see the SKILL) |

---

## See also

- [validation-annotations-reference.md](validation-annotations-reference.md) — the 22 built-in constraints
- [custom-validators-reference.md](custom-validators-reference.md) — custom `Validator<T>` implementations
