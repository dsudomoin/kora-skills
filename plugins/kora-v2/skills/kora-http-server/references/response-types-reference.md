# Response Types Reference

How a handler's return type becomes an HTTP response: built-in types, `@Json`,
`HttpResponseEntity<T>`, `HttpServerResponse`, and custom `HttpServerResponseMapper`.

## Contents

- [What the processor generates](#what-the-processor-generates)
- [Built-in return types](#built-in-return-types)
- [void / Unit](#void--unit)
- [HttpResponseEntity](#httpresponseentity)
- [HttpServerResponse](#httpserverresponse)
- [HttpBody and HttpHeaders](#httpbody-and-httpheaders)
- [Custom response mapping](#custom-response-mapping)
- [The @Component rule for mappers](#the-component-rule-for-mappers)
- [Kotlin nullability asymmetry](#kotlin-nullability-asymmetry)
- [Reactive and suspending returns are gone](#reactive-and-suspending-returns-are-gone)

---

## What the processor generates

The return type decides whether a mapper is injected at all. From the generator:

| Return type | Generated module parameter | Generated body |
|---|---|---|
| `void` / `Unit` | *(none)* | `_controller.m(...); return HttpServerResponse.of(200);` |
| `HttpServerResponse` | *(none)* | `return _controller.m(...);` |
| anything else | `HttpServerResponseMapper<T> _responseMapper` | `return _responseMapper.apply(_request, _result);` |
| any, with `@Mapping(X.class)` | `X _responseMapper` | `return _responseMapper.apply(_request, _result);` |

Reading the generated `<Controller>Module` tells you exactly which mapper the graph must supply.

---

## Built-in return types

`HttpServerResponseMapperModule` supplies these as `@DefaultComponent`s:

| Return type | Status | Content type |
|---|---|---|
| `String` | 200 | `text/plain` |
| `byte[]` | 200 | `application/octet-stream` |
| `ByteBuffer` | 200 | `application/octet-stream` |
| `HttpBodyOutput` | 200 | whatever the body declares |
| `HttpServerResponse` | as built | as built |
| `T` with `@Json` on the method | 200 | `application/json`, via the generated `JsonWriter<T>` |
| `HttpResponseEntity<T>` | as given | delegates to the `T` mapper |

```java
@HttpRoute(method = HttpMethod.GET, path = "/health")
public String health() {
    return "OK";                       // 200, text/plain
}

@HttpRoute(method = HttpMethod.GET, path = "/users/{id}")
@Json
public UserResponse get(@Path String id) {
    return userService.getUser(id);    // 200, application/json
}
```

`@Json` on the method is what selects the JSON mapper — without it, a plain DTO return type has no
mapper and the build fails with `No component found for dependency: HttpServerResponseMapper<UserResponse>`.

---

## void / Unit

A handler that returns nothing needs **no response mapper**. The processor special-cases it and
emits `HttpServerResponse.of(200)` — an empty 200:

```java
@HttpRoute(method = HttpMethod.DELETE, path = "/users/{userId}")
public void deleteUser(@Path String userId) {
    userService.delete(userId);
}
```

```kotlin
@HttpRoute(method = HttpMethod.DELETE, path = "/users/{userId}")
fun deleteUser(@Path userId: String) {
    userService.delete(userId)
}
```

Two caveats:

- **In Java, return `void`, not `Void`.** Only the primitive `void` is special-cased by the mapper
  detection, so a `Void`-returning method has an `HttpServerResponseMapper<Void>` parameter
  generated that nothing can satisfy — while the generated body never even uses it.
- **Attaching `@Mapping` to a `void`/`Unit` method** re-enables the mapper parameter, but the
  generated body still ignores it and returns `HttpServerResponse.of(200)`. With
  `@Mapping(X.class)` the parameter is typed `X`, so the graph must supply `X` for a mapper that
  is never called; with a tag-only `@Mapping` it is typed `HttpServerResponseMapper<Void>` /
  `<Unit>`, which nothing supplies. Either way it is pointless — if you want a status other than
  200, return `HttpServerResponse.of(204)` instead of mapping a void.

To return `204 No Content` explicitly:

```java
@HttpRoute(method = HttpMethod.DELETE, path = "/users/{userId}")
public HttpServerResponse deleteUser(@Path String userId) {
    userService.delete(userId);
    return HttpServerResponse.of(204);
}
```

---

## HttpResponseEntity

A body plus a status code and headers, with the body still serialized by the `T` mapper (JSON when
`@Json` is present).

```java
@HttpRoute(method = HttpMethod.POST, path = "/users")
@Json
public HttpResponseEntity<UserResponse> create(@Json UserRequest request) {
    var user = userService.create(request);
    return HttpResponseEntity.of(201, HttpHeaders.of("Location", "/users/" + user.id()), user);
}
```

Factories (`io.koraframework.http.common.HttpResponseEntity`):

```java
static <T> HttpResponseEntity<T> of(int code, T body);
static <T> HttpResponseEntity<T> of(int code, HttpHeaders headers, @Nullable T body);
```

There is no fluent `.status().header().body()` builder.

---

## HttpServerResponse

Full manual control. Returned as-is — no mapper is involved.

```java
static HttpServerResponse of(int code);
static HttpServerResponse of(int code, @Nullable HttpBodyOutput body);
static HttpServerResponse of(int code, @Nullable HttpHeaders headers);
static HttpServerResponse of(int code, @Nullable HttpHeaders headers, HttpBodyOutput body);
```

```java
return HttpServerResponse.of(200, HttpHeaders.of("X-Trace", traceId), HttpBody.plaintext("OK"));
return HttpServerResponse.of(200, HttpBody.json(jsonBytes));
return HttpServerResponse.of(204);
```

Use it for binary downloads, streaming, custom content types, or any non-JSON response.
Accessors: `code()`, `headers()`, `body()`.

---

## HttpBody and HttpHeaders

`io.koraframework.http.common.body.HttpBody`:

```java
HttpBody.empty()
HttpBody.plaintext(String | ByteBuffer)
HttpBody.json(String | byte[])
HttpBody.octetStream(byte[] | ByteBuffer)
HttpBody.of(byte[] | ByteBuffer)
HttpBody.of(@Nullable String contentType, byte[] | ByteBuffer)
```

`io.koraframework.http.common.header.HttpHeaders` — fixed-arity overloads, **not** a
`String...` varargs of pairs:

```java
HttpHeaders.empty()
HttpHeaders.of()                                          // empty MutableHttpHeaders
HttpHeaders.of("name", "value")
HttpHeaders.of("n1", "v1", "n2", "v2")                    // up to four pairs
HttpHeaders.of("name", List.of("v1", "v2"))               // repeated header
HttpHeaders.of(Map<String, List<String>>)
HttpHeaders.ofPlain(Map<String, String>)
```

Reading: `getFirst(name)`, `getAll(name)`, `has(name)`.

To produce JSON bytes inside a mapper or interceptor, inject a `JsonWriter<T>` and call
`writer.toByteArray(value)`. The 1.x `*Unchecked` variants are gone and the plain methods no
longer declare a checked exception — a leftover `try/catch (IOException)` is now a compile error.

---

## Custom response mapping

```java
public interface HttpServerResponseMapper<T> extends Mapping.MappingFunction {
    HttpServerResponse apply(HttpServerRequest request, @Nullable T result) throws IOException;
}
```

Two arguments — the 1.x `Context` first parameter is gone.

```java
@Component
@HttpController
public final class MapperResponseController {

    public record HelloWorldResponse(String greeting, String name) {}

    @Component
    public static final class HelloWorldResponseMapper implements HttpServerResponseMapper<HelloWorldResponse> {
        @Override
        public HttpServerResponse apply(HttpServerRequest request, HelloWorldResponse result) {
            return HttpServerResponse.of(200, HttpBody.plaintext(result.greeting() + " - " + result.name()));
        }
    }

    @HttpRoute(method = HttpMethod.GET, path = "/mapper/response/{name}")
    @Mapping(HelloWorldResponseMapper.class)
    public HelloWorldResponse get(@Path String name) {
        return new HelloWorldResponse("Hello World", name);
    }
}
```

You can also skip `@Mapping` and let the generic slot resolve: a `@Component` implementing
`HttpServerResponseMapper<HelloWorldResponse>` is picked up for any handler returning that type.

---

## The @Component rule for mappers

The rule follows from **which type the generated module asks for**, not from whether the mapper has
dependencies.

**With `@Mapping(X.class)`** the parameter is typed `X` — the concrete class:

```java
default HttpServerRequestHandler get_g_n(Ctl _controller, Ctl.GreetingMapper _responseMapper) { … }
```

so `X` must be a component. `@Component` is required **even for a mapper with no constructor
arguments**. Without it:

```
error: No component found for dependency:
    probe.Ctl.GreetingMapper (no tags)
  Fix:
    - Add @Component to an implementation of probe.Ctl.GreetingMapper.
    - Add a module method that returns probe.Ctl.GreetingMapper.
```

**Without `@Mapping`** the parameter is typed `HttpServerResponseMapper<T>`, and exactly one
non-default provider must match. Two `@Component` mappers for the same `T` collide:

```
error: Multiple components match dependency:
    io.koraframework.http.server.common.response.HttpServerResponseMapper<probe.Ctl2.Box> (no tags)
    Candidates:
    - component  probe.Ctl2.BoxMapper
    - component  probe.Ctl2.OtherBoxMapper
  Fix:
    - Add different @Tag(...) annotations to candidates and request the needed tag.
    - Mark fallback candidate with @DefaultComponent.
    - Remove one duplicate provider.
```

The framework's own mappers are `@DefaultComponent`, so your `@Component` overrides them without a
conflict — the clash only happens between two of your own.

---

## Kotlin nullability asymmetry

Kora's HTTP modules are `@NullMarked` (JSpecify, declared on `module-info.java`), and the contract
declares `result` as `@Nullable`. Kotlin enforces that exactly, so the override **must** take `T?`:

```kotlin
@Component
class HelloWorldResponseMapper : HttpServerResponseMapper<HelloWorldResponse> {
    // the contract declares the result as @Nullable, so Kotlin must accept null here
    override fun apply(request: HttpServerRequest, result: HelloWorldResponse?): HttpServerResponse {
        requireNotNull(result)
        return HttpServerResponse.of(200, HttpBody.plaintext("${result.greeting} - ${result.name}"))
    }
}
```

With `result: HelloWorldResponse` Kotlin reports:

```
'apply' overrides nothing
```

which never mentions nullability. This is the one place where a Java mapper compiles and its
literal Kotlin translation does not.

---

## Reactive and suspending returns are gone

Kora 2.0 contracts are synchronous, executed on virtual threads.

- `http-server-common/…/response/mapper/` contains exactly three files —
  `HttpServerResponseEntityMapper`, `HttpServerResponseMapperModule`,
  `JsonWriterHttpServerResponseMapper`. There is **no** mapper for `Mono`, `Flux` or
  `CompletionStage`, so such a return type fails with `No component found for dependency`.
- Kotlin `suspend` is rejected outright by KSP:

  ```
  Suspend methods are not supported by the HTTP server controller generator.
  ```

  The message goes on to suggest `--enable-preview` with `StructuredTaskScope`.

Run concurrent work inside a synchronous handler instead:

```java
try (var scope = StructuredTaskScope.open(StructuredTaskScope.Joiner.<Object>awaitAllSuccessfulOrThrow())) {
    var profile = scope.fork(() -> profileService.getProfile(userId));
    var recommendations = scope.fork(() -> recommendationService.getForUser(userId));
    scope.join();
    return new Dashboard(profile.get(), recommendations.get());
}
```

`StructuredTaskScope` is a preview API — enable preview features for `javac`, Kotlin, tests and the
launcher, and pin to the preview shape of the JDK you actually build with.

**See also:** [Error Handling](error-handling-reference.md), [Request Mapping](request-mapping-reference.md).
