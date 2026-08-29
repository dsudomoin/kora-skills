---
name: kora-openapi-generator-client
description: "Generate a Kora 2.x HTTP client from an OpenAPI 3.x contract with the `kora` generator (io.koraframework:openapi-generator, modes java-client / kotlin-client). Emits a @HttpClient-annotated *Api interface with synchronous methods, sealed *ApiResponses per status code, *ApiClientRequestMappers / *ApiClientResponseMappers, model records or data classes and an ApiSecurity @Module for securitySchemes. Use for contract-first outbound clients, clientConfig / clientConfigPrefix wiring, the lower-camel httpClient.<client>.<api> config key, enum fromValue parsing, HttpClientTokenProvider auth tags, or a client that starts green but hangs on every request."
license: Apache-2.0
metadata:
  kora-version: "2.x"
---

# Kora OpenAPI Generator — HTTP Client

> **Kora sub-skill — obey the [kora-v2 meta rules](../../SKILL.md) on every task:** **R0** ground the workspace on Kora 2.0 refs before starting (framework source at tag `2.0.0.RC1` + `kora-examples` at `migration/2.0`; `kora-docs` is 1.x only) · **R1** read this sub-skill before writing code · **R2** Kora 2.0 APIs only — no Spring/Micronaut/Quarkus, no Kora 1.x APIs, no invented annotations or config keys · **R3** journal any incorrect Kora usage. Add comments/Javadoc only if asked.

**Version:** Kora 2.0 (`io.koraframework`, `2.0.0.RC1` on Maven Central) | **Java:** 25 | **Kotlin:** 2.4 + KSP | **Gradle:** 9+

Generate a typed, declarative outbound HTTP client from an OpenAPI 3.x contract. The
`org.openapi.generator` Gradle plugin with `generatorName = "kora"` emits a `@HttpClient`
interface plus models at build time; inject the `*Api` into a `@Component` and call it.

This skill covers **clients only**. For generated server controllers and delegates use
`kora-openapi-generator-server`; for a hand-written `@HttpClient` use `kora-http-client`.

---

## Read this first: the client config key lower-cases the API name

This is the one change that costs the most time, because nothing fails loudly.

`clientConfigPrefix` is joined to the **generated API class name with its first letter
lower-cased**. `PetApi` becomes `petApi`, so a build with `clientConfigPrefix = "httpClient.petV2"`
produces `@HttpClient("httpClient.petV2.petApi")`:

```java
// io/koraframework/openapi/generator/KoraCodegen.java — clientConfigPath(String)
return params.clientConfigPrefix + "." + StringUtils.uncapitalize(clientName);
```

```hocon
# Kora 1.x — silently ignored in 2.0
httpClient.petV2.PetApi { url = ${PET_API_URL} }

# Kora 2.0
httpClient.petV2.petApi { url = ${PET_API_URL} }
```

**Why it hurts:** HOCON does not reject an unknown section. The stale block is simply never read,
the client comes up with no `url`, and requests do not fail fast — they **hang until
`requestTimeout` expires**. Tests time out instead of reporting a config error.

**Never guess the key.** Read it off the generated annotation, which is the only authority:

```bash
grep -rn '@HttpClient' build/generated/**/api/*Api.java   # or *Api.kt
```

The generator also prints the full mapping at the end of every client generation:

```
Generated Kora OpenAPI HTTP clients and config paths:
  - PetApi -> httpClient.petV2.petApi (configPath)
```

### `clientConfig` vs `clientConfigPrefix`

Client modes require exactly one of them; with neither, generation fails with
*"Missing OpenAPI generator `clientConfig`"*.

| Option | Emits | Use when |
|---|---|---|
| `clientConfigPrefix: "httpClient.petV2"` | `@HttpClient("httpClient.petV2.petApi")` — one section **per API class** | the spec has several tags, or you want one section per client |
| `clientConfig: "httpClient.petV2"` | `@HttpClient("httpClient.petV2")` — the **same** section for every API in the spec, nothing appended | a single-tag spec you want to configure once |

The API class name comes from the OpenAPI **tag**: `pets` → `PetsApi`, `pet` → `PetApi`, no tag at
all → `DefaultApi`. Rename a tag and the config key moves with it.

---

## Only four generator modes exist

`configOptions.mode` accepts exactly `java-client`, `java-server`, `kotlin-client`,
`kotlin-server` (`CodegenMode` in the generator). Anything else aborts generation with
*"Invalid OpenAPI generator `mode`"* plus the list of supported values.

| Removed in 2.0 | Use instead |
|---|---|
| `java-reactive-client`, `java-async-client` | `java-client` |
| `kotlin-suspend-client`, `kotlin-reactive-client` | `kotlin-client` |

Generated client methods are **synchronous** and run on virtual threads. There is no
`CompletionStage`, `Mono`/`Flux` or `suspend` variant, and `http-client-async` no longer exists as
an artifact. Changing the mode string is necessary but not sufficient — every call site that
awaited a client method has to become a direct call.

---

## Migrating from Kora 1.x

| Kora 1.x | Kora 2.x |
|---|---|
| BOM `ru.tinkoff.kora:kora-parent` | **`io.koraframework:kora-bom`** |
| `classpath("ru.tinkoff.kora:openapi-generator:…")` | **`classpath("io.koraframework:openapi-generator:$koraVersion")`** |
| `ru.tinkoff.kora:json-module` | **`io.koraframework:json-common`** |
| `ru.tinkoff.kora:http-client-async` | **removed** — use `http-client-ok`, `http-client-jdk` or `http-client-apache` |
| `httpClient.petV2.PetApi { … }` | **`httpClient.petV2.petApi { … }`** — first letter lower-cased |
| `<operationId>Config { … }` | **`<methodName> { … }`**, e.g. `getPetById { requestTimeout = 20s }` |
| `@HttpClient(configPath = "x")` | **`@HttpClient("x")`** — 2.0 declares `value()`, `telemetryTag()`, `httpClientTag()`; there is no `configPath` |
| reactive / async / suspend client modes | **removed** — see the table above |
| `Enum.valueOf(raw)` / `enumValueOf(raw)` | **`MyEnum.fromValue(raw)`** |
| `@Tag(ApiSecurity.SecurityRequirementTag1.class)` | **`@Tag(ApiSecurity.<SchemeName>.class)`**, e.g. `ApiSecurity.BearerAuth` |
| `configOptions.interceptors` | **`configOptions.extensions`** (`interceptorType` / `interceptorTag`) |
| `configOptions.additionalContractAnnotations` | **`extensions.*.additionalMethodAnnotations`** |
| `configOptions.authAllowMultiple`, `enableJsonNullable`, `forceIncludeNonRequired` | **removed** — no replacement option |
| `ru.tinkoff.kora.common.Component` | **`io.koraframework.common.annotation.Component`** |

Three of these fail only at runtime, never at compile time — the config key above, plus:

- **`fromValue`, not `valueOf`.** The wire value in the contract need not match the generated
  constant: `available` → `AVAILABLE`, `Dingo-Don` → `DINGO_DON`, `5` → `NUMBER_5`. The raw values
  live in a nested `Constants` holder, and `fromValue(raw)` is the only lookup that consults them.
  `Enum.valueOf("available")` throws `IllegalArgumentException` for perfectly valid input, and only
  once real data arrives.
- **Security tags are named after the security scheme.** `components.securitySchemes.bearerAuth`
  becomes the nested marker `ApiSecurity.BearerAuth`. Ordinal `SecurityRequirementTagN` names do
  not exist in 2.0, so a token provider tagged that way is never found and the graph fails to
  build. See [references/authorization-reference.md](references/authorization-reference.md).

---

## The Gradle process itself must run on JDK 25+

`io.koraframework:openapi-generator` goes on the **buildscript classpath**, which is resolved by
the JVM running Gradle — not by the project toolchain. A Java 25 `toolchain { }` block is not
enough. On an older Gradle JVM configuration fails with:

```
Dependency requires at least JVM runtime version 25. This build uses a Java 21 JVM.
```

Fix it where the Gradle JVM is chosen (`JAVA_HOME`, `org.gradle.java.home`, or the IDE's Gradle
JVM setting), not in the toolchain block.

---

## Quick Start

### 1. `gradle.properties`

`2.0.0.RC1` is the Kora 2.0 release on Maven Central and resolves from plain `mavenCentral()`.
`2.0.0-SNAPSHOT` is the development line; it needs
`https://central.sonatype.com/repository/maven-snapshots` and does not belong in a new project.

```properties
koraVersion=2.0.0.RC1
```

### 2. Build wiring

Two independent versions are in play here — do not conflate them:

| What | Where it is set | Value in the 2.0 examples |
|---|---|---|
| **OpenAPI Generator Gradle plugin** (`org.openapi.generator`) | `plugins { }` block | `7.23.0` (Java example) / `7.24.0` (Kotlin example) |
| **Kora `kora` generator** (`io.koraframework:openapi-generator`) | `buildscript { dependencies { classpath … } }` | `$koraVersion` = `2.0.0.RC1` |

The Kora generator is compiled against `org.openapitools:openapi-generator` **7.24.0** (framework
version catalog), so `7.24.0` is the aligned plugin choice.

```groovy title="build.gradle (Java)"
import org.openapitools.generator.gradle.plugin.tasks.GenerateTask

buildscript {
    repositories { mavenCentral() }
    dependencies {
        classpath "io.koraframework:openapi-generator:$koraVersion"
    }
}

plugins {
    id "java"
    id "application"
    id "org.openapi.generator" version "7.24.0"
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
        vendor = JvmVendorSpec.ADOPTIUM
    }
}

configurations {
    koraBom
    annotationProcessor.extendsFrom(koraBom)
    compileOnly.extendsFrom(koraBom)
    implementation.extendsFrom(koraBom)
    testImplementation.extendsFrom(koraBom)
    testAnnotationProcessor.extendsFrom(koraBom)
}

dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")
    annotationProcessor "io.koraframework:annotation-processors"   // MANDATORY

    implementation "io.koraframework:http-client-ok"               // transport — pick exactly one
    implementation "io.koraframework:json-common"
    implementation "io.koraframework:config-hocon"
    implementation "io.koraframework:logging-logback"
}
```

```kotlin title="build.gradle.kts (Kotlin)"
import org.openapitools.generator.gradle.plugin.tasks.GenerateTask

buildscript {
    repositories { mavenCentral() }
    dependencies {
        classpath("io.koraframework:openapi-generator:${property("koraVersion")}")
    }
}

plugins {
    id("application")
    kotlin("jvm") version "2.4.10"
    id("com.google.devtools.ksp") version "2.3.11"
    id("org.openapi.generator") version "7.24.0"
}

kotlin {
    jvmToolchain {
        languageVersion.set(JavaLanguageVersion.of(25))
        vendor.set(JvmVendorSpec.ADOPTIUM)
    }
}

dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))
    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")   // MANDATORY

    implementation("io.koraframework:http-client-ok")
    implementation("io.koraframework:json-common")
    implementation("io.koraframework:config-hocon")
    implementation("io.koraframework:logging-logback")
}
```

Transports and their `@KoraApp` modules:

| Artifact | Module | Package |
|---|---|---|
| `io.koraframework:http-client-ok` | `OkHttpClientModule` | `io.koraframework.http.client.ok` |
| `io.koraframework:http-client-jdk` | `JdkHttpClientModule` | `io.koraframework.http.client.jdk` |
| `io.koraframework:http-client-apache` | `ApacheHttpClientModule` | `io.koraframework.http.client.apache` |

The generator is transport-agnostic — the same generated `*Api` works with any of them. One
caveat specific to `2.0.0.RC1`: the Apache and JDK transport integrations received correctness
fixes *after* the RC1 tag, so on the released version `http-client-ok` is the safer default. (The
migrated Java example does use `http-client-apache`; if you pick it, exercise it against the real
service rather than assuming.)

### 3. Generation task — one per spec, unique `outputDir`

```groovy title="build.gradle (Java)"
def openApiGeneratePetV2 = tasks.register("openApiGeneratePetV2", GenerateTask) {
    generatorName = "kora"
    group = "openapi tools"
    inputSpec = layout.projectDirectory.file("src/main/resources/openapi/petstoreV2.yaml")
    outputDir = layout.buildDirectory.dir("generated/openapi/petV2")
    def corePackage = "com.example.openapi.petV2"
    apiPackage = "${corePackage}.api"
    modelPackage = "${corePackage}.model"
    invokerPackage = "${corePackage}.invoker"
    configOptions = [
            mode              : "java-client",
            clientConfigPrefix: "httpClient.petV2",   // -> @HttpClient("httpClient.petV2.petApi")
    ]
}
sourceSets.main { java.srcDirs += openApiGeneratePetV2.get().outputDir }
compileJava.dependsOn openApiGeneratePetV2
```

```kotlin title="build.gradle.kts (Kotlin)"
val openApiGeneratePetV2 = tasks.register<GenerateTask>("openApiGeneratePetV2") {
    generatorName.set("kora")
    group = "openapi tools"
    inputSpec.set("$projectDir/src/main/resources/openapi/petstoreV2.yaml")
    outputDir.set(layout.buildDirectory.dir("generated/openapi/petV2").get().asFile.absolutePath)
    val corePackage = "com.example.openapi.petV2"
    apiPackage.set("$corePackage.api")
    modelPackage.set("$corePackage.model")
    invokerPackage.set("$corePackage.invoker")
    configOptions.set(
        mapOf(
            "mode" to "kotlin-client",
            "clientConfigPrefix" to "httpClient.petV2",
        )
    )
}

kotlin.sourceSets.main { kotlin.srcDir(openApiGeneratePetV2.get().outputDir) }
// KSP must see the generated sources, so every ksp* task depends on generation
tasks.matching { it.name.startsWith("ksp") }.configureEach { dependsOn(openApiGeneratePetV2) }
tasks.compileKotlin { dependsOn(openApiGeneratePetV2) }
```

Every spec gets its **own `outputDir`**. Sharing one breaks incremental builds and leaves stale
classes behind.

### 4. Plug the modules into `@KoraApp`

```java title="Application.java"
@KoraApp
public interface Application extends
        HoconConfigModule,      // io.koraframework.config.hocon
        LogbackModule,          // io.koraframework.logging.logback
        JsonModule,             // io.koraframework.json.common
        OkHttpClientModule {    // io.koraframework.http.client.ok

    static void main(String[] args) {
        KoraApplication.run(ApplicationGraph::graph);
    }
}
```

A client-only application does **not** need an HTTP server module. Note that
`io.koraframework.validation.module.ValidationModule` pulls `http-server-common` onto the
classpath through `ViolationExceptionHttpServerResponseMapper`; if you only need the constraint
validators, extend `io.koraframework.validation.common.constraint.ValidatorModule` instead.

### 5. Inject and call the generated `*Api`

Generated methods return a **sealed `*ApiResponses` wrapper**, one variant per declared status
code — never the bare model.

```java title="PetService.java"
@Component
public final class PetService {

    private final PetApi petApi;

    public PetService(PetApi petApi) {
        this.petApi = petApi;
    }

    public Pet getPet(long petId) {
        return switch (petApi.getPetById(petId)) {
            case PetApiResponses.GetPetByIdApiResponse.GetPetById200ApiResponse ok -> ok.content();
            case PetApiResponses.GetPetByIdApiResponse.GetPetById404ApiResponse ignored ->
                    throw new NoSuchElementException("pet " + petId);
        };
    }
}
```

```kotlin title="PetService.kt"
@Component
class PetService(private val petApi: PetApi) {

    fun getPet(petId: Long): Pet = when (val response = petApi.getPetById(petId)) {
        // Kotlin variants are data classes: `content` is a property, not a method
        is PetApiResponses.GetPetByIdApiResponse.GetPetById200ApiResponse -> response.content
        is PetApiResponses.GetPetByIdApiResponse.GetPetById404ApiResponse ->
            throw NoSuchElementException("pet $petId")
    }
}
```

In Java the variants are `record`s, so the payload is `ok.content()`. In Kotlin they are
`data class`es, so it is `response.content`. A variant with no body is an empty `record` / a plain
`class`, with no `content` at all.

### 6. Configure the client (HOCON)

```hocon title="application.conf"
httpClient.petV2.petApi {          # <clientConfigPrefix>.<apiName with first letter lower-cased>
  url = ${PET_API_URL}
  requestTimeout = 10s

  getPetById {                     # per-operation override: the generated METHOD name, no suffix
    requestTimeout = 20s
  }

  telemetry.logging.enabled = true # logging and metrics default to false in 2.0
}
```

Per-operation sections are named after the generated method (`getPetById`, `listPets`), because
the HTTP client processor derives the generated `$PetApi_Config` accessors straight from the
interface methods. The 1.x `<operationId>Config` spelling is not read.

---

## What gets generated

For `apiPackage = com.example.openapi.petV2.api`, tag `pet`:

| File | Contents |
|---|---|
| `PetApi` | `@HttpClient("…")` interface, one synchronous method per operation, `@HttpRoute` / `@Path` / `@Query` / `@Header` / `@Json`, `@ResponseCodeMapper` per status code |
| `PetApiResponses` | nested `sealed interface <Op>ApiResponse` with a record/data class per status code; the OpenAPI `default` response becomes `<Op>DefaultApiResponse(int statusCode, T content)` |
| `PetApiClientResponseMappers` | one `HttpClientResponseMapper` per response variant |
| `PetApiClientRequestMappers` | request body / form mappers where needed |
| `PetApi<Op>OptArgs` | an optional-argument holder plus overloads, when an operation has optional parameters |
| `ApiSecurity` | a `@Module` with a marker class per security scheme, a `SecurityConfig` record and `HttpClientTokenProvider` components — only when the spec declares `securitySchemes` |
| model package | records (Java, `@JsonWriter` + `@JsonReader`) / data classes (Kotlin, `@Json`); polymorphic schemas become a `sealed interface` with `@JsonDiscriminatorField` |

`$PetApi_ClientImpl`, `$PetApi_Config` and `$PetApi_Module` are produced afterwards by the Kora
annotation processor / KSP from the generated interface — they are not OpenAPI generator output.

---

## Core rules

1. **Never hand-edit generated code.** After renaming `apiPackage` / `modelPackage` or moving
   `outputDir`, run `./gradlew clean build --no-build-cache` once: the generator does not delete
   its previous output and the build-cache key ignores the package change, so the old and new
   packages coexist in one source set and you get phantom errors from classes nobody imports.
2. **Parse enums with `fromValue`.** Never `Enum.valueOf` / `enumValueOf` on a wire value.
3. **Kotlin: construct models with named arguments.** The constructor parameter order mirrors the
   spec's property declaration order, and optional properties carry `= null` /
   `= JsonNullable.nullValue()` defaults. Reorder two same-typed properties in the contract and a
   positional call still compiles while silently swapping the values.
4. **Do not mark a generated client method `suspend`.** `kotlin-client` emits plain functions and
   the interface is generated, so there is nothing to annotate. If the caller must stay
   coroutine-based, put the bridge in your own service — and decide deliberately whether it is
   needed at all: the call already runs on a virtual thread, so `withContext(Dispatchers.IO)` buys
   nothing.
5. **One `GenerateTask` per spec, one `outputDir` per task.**
6. **Read the config key off the generated `@HttpClient`**, never from memory.

---

## Common pitfalls

| Symptom | Cause / fix |
|---|---|
| Client starts fine, every request hangs until `requestTimeout` | Config section not read — the key must be `httpClient.pet.petApi`, not `httpClient.pet.PetApi`. Verify against the generated `@HttpClient` |
| Generation fails: *"Missing OpenAPI generator `clientConfig`"* | A client mode needs `clientConfig` **or** `clientConfigPrefix` in `configOptions` |
| Generation fails: *"Invalid OpenAPI generator `mode`"* | Only `java-client`, `java-server`, `kotlin-client`, `kotlin-server` exist |
| `IllegalArgumentException: No enum constant …` at runtime, on valid data | `Enum.valueOf(raw)` instead of `MyEnum.fromValue(raw)` |
| Per-operation timeout ignored | Section must be the generated method name (`getPetById`), not `getPetByIdConfig` |
| `No component found for dependency: HttpClientTokenProvider` | The spec declares a `bearer` or `oauth2` scheme; the generator emits the tag but no provider. Supply `@Tag(ApiSecurity.BearerAuth.class) HttpClientTokenProvider` yourself |
| Request carries the wrong credential | The generated group interceptor takes the **first** provider returning a non-null token; a scheme you do not use must return `null` |
| `Required dependency PetApi not found` | No transport module on `@KoraApp`, or the Kora annotation processor / KSP is missing |
| Phantom `ru.tinkoff.kora` or old-package errors from `build/generated` | Stale generator output — `clean` + `--no-build-cache`, never edit generated files |
| An HTTP **server** artifact appears in a client-only app | `ValidationModule` drags in `http-server-common`; use `ValidatorModule` from `validation-common` |
| Unexpected `oneOf`/`anyOf` output | Plugin ≥ 7.0.0 enables `SIMPLIFY_ONEOF_ANYOF`; set `openapiNormalizer = [DISABLE_ALL: "true"]` |
| `4XX` / `5XX` range responses not generated | Not supported at `2.0.0.RC1` — declare exact codes plus `default`. Range support landed after RC1 |
| `Dependency requires at least JVM runtime version 25` | The **Gradle JVM** is too old; a toolchain block does not fix a buildscript dependency |

---

## References

| File | Purpose |
|---|---|
| [references/openapi-codegen-reference.md](references/openapi-codegen-reference.md) | Every `configOptions` key, config-path derivation, `extensions`, `tags`, normalizer, discriminators, response shapes |
| [references/authorization-reference.md](references/authorization-reference.md) | `securitySchemes` → `ApiSecurity`, `HttpClientTokenProvider` tags, credential config paths, `authAsMethodArgument` |

## Assets

| File | Purpose |
|---|---|
| [assets/build.gradle.client.template](assets/build.gradle.client.template) | Ready-to-edit Java client `build.gradle` |
| [assets/Application.client.java.template](assets/Application.client.java.template) · [assets/Application.client.kt.template](assets/Application.client.kt.template) | `@KoraApp` module wiring for a client-only app |
| [assets/PetService.client.java.template](assets/PetService.client.java.template) · [assets/PetService.client.kt.template](assets/PetService.client.kt.template) | `*Api` injection and sealed-response handling |
| [assets/openapi-spec.yaml.template](assets/openapi-spec.yaml.template) | Example OpenAPI 3.x contract (enums, discriminator, apiKey scheme) |
| [scripts/validate_openapi.py](scripts/validate_openapi.py) | Pre-generation linter; also prints the derived client config keys |
