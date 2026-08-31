# OpenAPI Controllers Reference — Kora 2.x

The generated `*ApiController` is the HTTP boundary. You never write it, never edit it and never
duplicate it with a hand-written `@HttpController` for the same operations. This document
describes what it looks like so you can read it when debugging.

## Contents

- [1. The generated controller](#1-the-generated-controller)
- [2. How routes actually get registered](#2-how-routes-actually-get-registered)
- [3. `prefixPath`](#3-prefixpath)
- [4. Per-operation interceptors](#4-per-operation-interceptors)
- [5. Global interceptors](#5-global-interceptors)
- [6. Validation on the controller](#6-validation-on-the-controller)
- [7. `final` vs open, and why aspects care](#7-final-vs-open-and-why-aspects-care)
- [8. Never hand-write a controller for a generated API](#8-never-hand-write-a-controller-for-a-generated-api)

---

## 1. The generated controller

```java
@Generated("io.koraframework.openapi.generator.javagen.ServerApiGenerator")
@Component
@HttpController()
public final class PetsApiController {
  private final PetsApiDelegate delegate;

  public PetsApiController(PetsApiDelegate delegate) {
    this.delegate = delegate;
  }

  @HttpRoute(method = "GET", path = "/pets/{petId}")
  @Mapping(PetsApiServerResponseMappers.ShowPetByIdApiResponseMapper.class)
  public PetsApiResponses.ShowPetByIdApiResponse showPetById(@Path("petId") String petId)
      throws Exception {
    return this.delegate.showPetById(petId);
  }
}
```

```kotlin
@Generated("io.koraframework.openapi.generator.kotlingen.ServerApiGenerator")
@Component
@HttpController("")
public class PetsApiController(
  public val `delegate`: PetsApiDelegate,
) {
  @HttpRoute(method = "GET", path = "/pets/{petId}")
  @Mapping(value = PetsApiServerResponseMappers.ShowPetByIdApiResponseMapper::class)
  public fun showPetById(@Path(value = "petId") petId: String): PetsApiResponses.ShowPetByIdApiResponse =
    this.delegate.showPetById(petId)
}
```

Annotation packages, all `io.koraframework`:

| Annotation | Package |
|---|---|
| `@Component`, `@Module`, `@Tag`, `@Mapping`, `@DefaultComponent`, `@Generated` | `common.annotation` |
| `@HttpController` | `http.server.common.annotation` |
| `@HttpRoute`, `@Path`, `@Query`, `@Header`, `@Cookie`, `@InterceptWith` | `http.common.annotation` |
| `@Json` | `json.common.annotation` |
| `@Valid`, `@Validate`, `@Range`, `@Size`, `@Pattern`, … | `validation.common.annotation` |
| `HttpServerRequest` / `HttpServerResponse` / `HttpServerInterceptor` | `http.server.common.request` / `.response` / `.interceptor` |

The controller holds no logic: it binds parameters, calls the delegate, and names the response
mapper. That is why the delegate is the only implementation point.

## 2. How routes actually get registered

Two code generators run in sequence:

1. **The OpenAPI generator** emits `PetsApiController` with `@HttpController` / `@HttpRoute`.
2. **The Kora HTTP-server annotation processor (Java) or KSP (Kotlin)** reads that controller and
   emits `PetsApiControllerModule`, a `@Module` interface with one
   `HttpServerRequestHandler`-returning method per route:

```java
@Generated("io.koraframework.http.server.annotation.processor.ControllerModuleGenerator")
@Module
public interface PetsApiControllerModule {
  default HttpServerRequestHandler get_pets_petId(PetsApiController _controller,
      PetsApiServerResponseMappers.ShowPetByIdApiResponseMapper _responseMapper) {
    return HttpServerRequestHandlerImpl.of("GET", "/pets/{petId}", _request -> { … });
  }
}
```

`@Module` interfaces are discovered automatically by the `@KoraApp` processor, so nothing needs
listing in the app interface. Practical consequences:

- **`io.koraframework:annotation-processors` (Java) or `io.koraframework:symbol-processors` via
  `ksp` (Kotlin) is mandatory.** Without it the controller compiles but no route exists and every
  request 404s.
- The generated handler parses path/query/header/cookie parameters and converts a parse failure
  into `HttpServerResponseException.of(400, …)` before your delegate is reached.
- The JSON body is read through an injected `@Tag(Json.class) HttpServerRequestMapper<T>`, so
  `JsonModule` must be in the graph.

## 3. `prefixPath`

`configOptions.prefixPath` fills the `@HttpController` value, prefixing every route in that
controller.

```groovy
configOptions = [mode: "java-server", prefixPath: '"/api/v1"']
```

The two languages emit it differently: the Kotlin generator writes it as a quoted string
(`addMember("%S", prefixPath)`), the Java generator writes it into the annotation verbatim
(`addMember("value", params.prefixPath)`). In `java-server` mode pass the value **already
quoted**, as above. In `kotlin-server` mode pass it bare: `"prefixPath" to "/api/v1"`.

The default is `""`, which renders as `@HttpController()` in Java and `@HttpController("")` in Kotlin. The lowest-risk option is to
leave `prefixPath` unset and write the prefix into the contract's `paths` — then the spec and the
served URLs cannot drift apart.

## 4. Per-operation interceptors

There are two sources of `@InterceptWith` on a generated controller method.

**Security**, derived from the contract, needs no configuration:

```java
@InterceptWith(value = HttpServerInterceptor.class, tag = ApiSecurity.ApiKeyAuth.class)
```

**Your own**, declared through `configOptions.extensions`:

```groovy
configOptions = [
    mode      : "java-server",
    extensions: """
        {
          "*": { "interceptorType": "com.example.api.AuditHttpServerInterceptor" },
          "operations": {
            "deletePet": { "interceptorTag": ["com.example.api.Tags.Admin"] }
          }
        }
        """,
]
```

- `interceptorType` alone → `@InterceptWith(TheType.class)`.
- `interceptorTag` alone → `@InterceptWith(value = HttpServerInterceptor.class, tag = TheTag.class)`,
  i.e. the framework interceptor type resolved by tag.
- Both → the given type resolved by tag.
- Sections are `"*"` (global), `"tags"` (keyed by the OpenAPI tag `baseName`) and `"operations"`
  (keyed by `operationId`); all matching sections apply.

There is **no** `configOptions.interceptors` key in Kora 2.0 — `CodegenParams` does not read it,
so a task that still passes it (some 1.x-era build files do) silently generates no interceptor at
all. Use `extensions`.

An interceptor implements
`io.koraframework.http.server.common.interceptor.HttpServerInterceptor`:

```java
@Component
public final class AuditHttpServerInterceptor implements HttpServerInterceptor {
    @Override
    public HttpServerResponse intercept(HttpServerRequest request, InterceptChain chain) throws Exception {
        return chain.process(request);
    }
}
```

## 5. Global interceptors

To intercept everything, including generated controllers, register the interceptor under the
framework's global tag:

```java
@Tag(HttpServer.class)
@Component
public final class TracingHttpServerInterceptor implements HttpServerInterceptor { … }
```

`HttpServerModule` collects them as `@Tag(HttpServer.class) All<HttpServerInterceptor>`, where
`HttpServer` is `io.koraframework.http.server.common.HttpServer`. The Kora 1.x tag
`@Tag(HttpServerModule.class)` still **compiles** — the type exists — but nothing looks
interceptors up by it, so the interceptor silently never runs. Cover it with a test.

## 6. Validation on the controller

With `enableServerValidation: "true"` the generator adds, per method:

```java
@InterceptWith(ValidationHttpServerInterceptor.class)   // unless enableServerValidationInterceptor = false
@Validate
public PetsApiResponses.ListPetsApiResponse listPets(
    @Query("filter") @Pattern(".*") String filter,
    @Query("limit") @Range(from = 1.0, to = 100.0, boundary = Range.Boundary.INCLUSIVE_INCLUSIVE)
    @Nullable Integer limit) throws Exception { … }
```

and `@Valid` on model-typed body parameters. See
[Validation Reference](openapi-validation-reference.md).

## 7. `final` vs open, and why aspects care

Kora aspects (`@Validate`, `@InterceptWith` on the method, extension-supplied annotations) are
implemented by a generated subclass, so the class must be extendable.

- Java: the controller is emitted `final` **unless** `enableServerValidation` is on or an
  `extensions` section contributes `additionalMethodAnnotations`.
- Kotlin: the controller and its functions gain `open` under the same condition.

If you add an aspect-bearing annotation through `extensions.*.additionalMethodAnnotations`, the
generator opens the class for you. If you were to add one by hand to generated code — don't —
the aspect processor would fail on a `final` class.

## 8. Never hand-write a controller for a generated API

Writing your own `@HttpController` with the same `@HttpRoute` values registers a second handler
for the same method+path. Symptoms are non-deterministic: one handler wins, the other never runs,
and which one depends on graph ordering.

If the contract cannot express what you need:

| Need | Correct move |
|---|---|
| A header the contract does not declare | `requestInDelegateParams: "true"`, read it from `HttpServerRequest` |
| An extra route next to the API | A separate `@HttpController` on **different** paths |
| A different wire shape for one response | Declare your own `@Component` `HttpServerResponseMapper` for that response type — the generated one is `@DefaultComponent` |
| Cross-cutting behaviour | An `HttpServerInterceptor` via `extensions`, or globally via `@Tag(HttpServer.class)` |
| A different status | Declare it in the contract and regenerate |

## Related

- [Delegates Reference](openapi-delegates-reference.md)
- [Response Reference](openapi-response-reference.md)
- [Authorization Reference](authorization-reference.md)
- [Advanced Codegen](advanced-codegen-reference.md)
