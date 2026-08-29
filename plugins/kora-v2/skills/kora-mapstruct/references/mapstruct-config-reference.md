# Build Configuration Reference — MapStruct (Java) and Konvert (Kotlin)

**Verified against** the Kora 2.0 module layout at tag `2.0.0.RC1`
(<https://github.com/kora-projects/kora/tree/2.0.0.RC1/mapping>), Kora's `gradle/libs.versions.toml`,
and the working builds on `kora-examples` branch `migration/2.0`:
[`kora-java-crud`](https://github.com/kora-projects/kora-examples/tree/migration/2.0/examples/java/kora-java-crud)
and
[`kora-kotlin-crud`](https://github.com/kora-projects/kora-examples/tree/migration/2.0/examples/kotlin/kora-kotlin-crud).

## Contents

- [Artifacts: what exists and what you declare](#artifacts-what-exists-and-what-you-declare)
- [Java setup — MapStruct](#java-setup--mapstruct)
- [Kotlin setup — Konvert](#kotlin-setup--konvert)
- [Kotlin + MapStruct: the only supported route](#kotlin--mapstruct-the-only-supported-route)
- [Versions](#versions)
- [componentModel](#componentmodel)
- [Shared configuration with @MapperConfig](#shared-configuration-with-mapperconfig)
- [Troubleshooting](#troubleshooting)

## Artifacts: what exists and what you declare

`ru.tinkoff.kora:mapstruct-extension` **does not exist in Kora 2.0.** It split into a javac half and a
KSP half; a third module, `konvert-ksp-extension`, is new in 2.0. All three are under
`io.koraframework` and all three are constrained by the `2.0.0.RC1` BOM:

| Artifact | Purpose |
|---|---|
| `io.koraframework:mapstruct-java-extension` | binds MapStruct-generated impls during javac annotation processing |
| `io.koraframework:mapstruct-ksp-extension` | binds an already-compiled MapStruct impl from a KSP build |
| `io.koraframework:konvert-ksp-extension` | binds Konvert-generated `object <Name>Impl` from a KSP build |

**You do not declare any of them.** They are `api` dependencies of the aggregate processors:

- `io.koraframework:annotation-processors` → `mapstruct-java-extension`
- `io.koraframework:symbol-processors` → `mapstruct-ksp-extension`, `konvert-ksp-extension`

Both aggregates are constrained by `io.koraframework:kora-bom`, so no version goes on them (except
the Kotlin `ksp(...)` line, which the BOM does not reach).

**`kora-bom` does not constrain third-party artifacts** — it is a `java-platform` built from Kora's
own subprojects. `org.mapstruct:*` and `io.mcarle:*` carry explicit versions, always.

## Java setup — MapStruct

`gradle.properties`:

```properties
koraVersion=2.0.0.RC1
```

`build.gradle`:

```groovy
repositories { mavenCentral() }

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
        vendor = JvmVendorSpec.ADOPTIUM
    }
}

configurations {
    koraBom
    annotationProcessor.extendsFrom(koraBom); compileOnly.extendsFrom(koraBom)
    implementation.extendsFrom(koraBom); api.extendsFrom(koraBom)
    testImplementation.extendsFrom(koraBom); testAnnotationProcessor.extendsFrom(koraBom)
}

dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")

    annotationProcessor "io.koraframework:annotation-processors"
    annotationProcessor "org.mapstruct:mapstruct-processor:1.6.3"

    implementation "org.mapstruct:mapstruct:1.6.3"
}
```

Three things must all be true, or the mapper never reaches the graph:

1. `annotation-processors` on `annotationProcessor` — brings `mapstruct-java-extension` and the
   `KoraAppProcessor` that consults it.
2. `mapstruct-processor` on `annotationProcessor` — the only thing that generates `*MapperImpl`.
3. `mapstruct` on `implementation` (compile classpath) — `MapstructKoraExtensionFactory` resolves
   `org.mapstruct.Mapper` through the processing environment and returns *no extension* when the
   type is absent. Processor-only wiring fails with a plain unresolved-dependency error that never
   mentions MapStruct.

The `koraBom` configuration with `extendsFrom` is not optional in Java: a `platform` on
`implementation` does not reach the `annotationProcessor` classpath, so `annotation-processors`
would fail to resolve.

### Declaration order does not matter

`kora-java-crud` lists `mapstruct-processor` before `annotation-processors`. That ordering is not
load-bearing. `KoraAppProcessor` builds the graph only when `roundEnv.processingOver()` — the final
annotation-processing round — so every `*MapperImpl` generated in an earlier round is already
visible. Kora's own extension test registers the processors in the opposite order and passes.

### Optional MapStruct compiler options

Standard MapStruct options, unrelated to Kora — see
<https://mapstruct.org/documentation/stable/reference/html/#configuration-options>:

```groovy
compileJava {
    options.compilerArgs += [
        "-Amapstruct.suppressGeneratorTimestamp=true",
        "-Amapstruct.suppressGeneratorVersionInfoComment=true"
    ]
}
```

Do not set `-Amapstruct.defaultComponentModel` to a DI framework's model on a whim; see
[componentModel](#componentmodel).

## Kotlin setup — Konvert

`build.gradle.kts`:

```kotlin
plugins {
    kotlin("jvm") version "2.4.10"
    id("com.google.devtools.ksp") version "2.3.11"
}

repositories { mavenCentral() }

kotlin {
    jvmToolchain {
        languageVersion.set(JavaLanguageVersion.of(25))
        vendor.set(JvmVendorSpec.ADOPTIUM)
    }
}

dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))

    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")
    ksp("io.mcarle:konvert:4.5.1")

    implementation("io.mcarle:konvert-api:4.5.1")
}
```

Kotlin does **not** use a `koraBom` configuration and does **not** use `extendsFrom`; the `ksp`
dependency on `symbol-processors` carries an explicit version because the BOM is not applied to the
`ksp` configuration. `konvert-api` on `implementation` is what activates
`KonvertKoraExtensionFactory` — same rule as MapStruct's.

If another generator must run before KSP (OpenAPI, protobuf), order it explicitly:

```kotlin
tasks.matching { it.name.startsWith("ksp") }.configureEach {
    dependsOn(openApiGenerateHttpServer)
}
```

## Kotlin + MapStruct: the only supported route

**kapt is not part of Kora 2.0.** The 1.x recipe — `kotlin("kapt")`, `kapt("…mapstruct-processor")`,
`KspTask.dependsOn(kaptKotlin)`, `allowSourcesFromOtherPlugins`, pinning Kotlin 1.9.10 — was deleted
from the corpus, not merely deprecated:

- The 2.0 migration commit in `kora-examples` removed every `kapt` line from
  `kora-kotlin-crud/build.gradle.kts`. They had already been commented out with the note
  `// KAPT & KSP broken since 1.9.11`.
- `kapt` appears **nowhere** in the migrated examples, guides or migration scripts.
- The follow-up commit replaced the hand-written Kotlin mappers with Konvert.
- Structurally: MapStruct ships only a javac annotation processor
  (`org.mapstruct.ap.MappingProcessor`), KSP does not run javac processors, and
  `mapstruct-ksp-extension` contains no generator — it resolves `<Name>Impl` via
  `resolver.getClassDeclarationByName` and binds its constructor. Its own test hand-writes
  `CarMapperImpl.kt` rather than running MapStruct.

So a `@Mapper` interface written in **Kotlin** source has nothing to generate its implementation in a
KSP-only build.

`mapstruct-ksp-extension` exists for the case where the implementation is **already on the compile
classpath** — a mapper declared in a Java module of the same project, or in a published jar, where
javac plus `mapstruct-processor` produced `FooMapperImpl`. A Kotlin `@KoraApp` can then inject
`FooMapper` and the extension binds it.

```
:mappers   (Java)   @Mapper FooMapper  +  annotationProcessor mapstruct-processor  →  FooMapperImpl
:app       (Kotlin) implementation(project(":mappers"))  +  ksp(symbol-processors)
                    constructor-injects FooMapper; mapstruct-ksp-extension binds FooMapperImpl
```

The Kotlin module still needs `org.mapstruct:mapstruct` on its own compile classpath (it comes
transitively if `:mappers` exposes it via `api`), otherwise the extension factory disables itself.

**Default advice: on Kotlin, use Konvert.** Reach for the cross-module route only when an existing
MapStruct mapper must be reused as-is.

## Versions

| Library | Kora catalog (`gradle/libs.versions.toml`) | `kora-examples` on `migration/2.0` | Use |
|---|---|---|---|
| `org.mapstruct:mapstruct` / `:mapstruct-processor` | **`1.6.3`** | `1.5.5.Final` | `1.6.3` |
| `io.mcarle:konvert-api` / `io.mcarle:konvert` | `4.5.1` (catalog lists `konvert-api` only) | `4.5.1` (both) | `4.5.1` |
| `io.koraframework:*` | — | `2.0.0.RC1` | from `kora-bom` |

The MapStruct disagreement is real, not a typo on either side. `1.6.3` is what Kora's own
`mapstruct-java-extension` and `mapstruct-ksp-extension` are compiled and tested against.
`1.5.5.Final` predates the 2.0 work in the examples: it was introduced by the earlier
*"Refactored structure and more Kotlin examples"* commit, and the 2.0 migration commit rewrote Kora
coordinates without touching it. **Pick `1.6.3`**, and keep the API and the processor on the same
version — a split pin produces MapStruct's own errors, which say nothing about Kora.

Toolchain floor for consuming Kora 2.0: **JDK 25**, Kotlin **2.4.10**, KSP **2.3.11**.

## componentModel

Kora reads no MapStruct component-model annotation. It binds the generated implementation's single
public constructor and nothing else. Plain `@Mapper` is correct and is what every example uses:

```java
@Mapper                                     // ✔ default
@Mapper(unmappedTargetPolicy = ReportingPolicy.IGNORE)   // ✔ also just MapStruct config
```

`componentModel` becomes relevant only to force **constructor injection of `uses` mappers**:

```java
@Mapper(uses = DateMapper.class,
        injectionStrategy = org.mapstruct.InjectionStrategy.CONSTRUCTOR,
        componentModel = "jakarta")
public interface CarMapper { … }
```

Then MapStruct emits a constructor taking `DateMapper`, and Kora resolves it from the graph. This
requires `jakarta.inject:jakarta.inject-api` on the compile classpath for the generated
`jakarta.inject` annotations to compile — Kora's extension module adds exactly that dependency for
its own test of this shape. Do not set `componentModel = "spring"` or `"cdi"`: those generate
framework annotations Kora ignores and dependencies nothing satisfies.

The one hard requirement is structural: **exactly one public constructor** on the generated impl,
or Kora reports *"Generated class `…` must have exactly one public constructor so Kora can use it as
a dependency."*

## Shared configuration with @MapperConfig

Plain MapStruct, transparent to Kora:

```java
@MapperConfig(
    unmappedTargetPolicy = ReportingPolicy.IGNORE,
    nullValuePropertyMappingStrategy = NullValuePropertyMappingStrategy.IGNORE
)
public interface MappingConfig {}

@Mapper(config = MappingConfig.class)
public interface OrderMapper { … }
```

The `@MapperConfig` interface itself is not a mapper — it has no `@Mapper`, so Kora never looks at
it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Could not find io.koraframework:mapstruct-extension` | 1.x coordinate. Nothing replaces it — the extension is already inside `annotation-processors` / `symbol-processors` |
| Unresolved dependency on `FooMapper`, no MapStruct message at all | The mapping library is missing from the compile classpath, so the extension factory returned nothing. Add `implementation "org.mapstruct:mapstruct:1.6.3"` (or `konvert-api`) |
| `MapStruct mapper implementation was not generated for FooMapper` in a **Java** module | `mapstruct-processor` is not on `annotationProcessor`, or MapStruct itself errored earlier in the same compile — read the errors above this one first |
| The same error in a **Kotlin** module | Expected: KSP does not run MapStruct. Use Konvert, or move the mapper into a Java module |
| `Generated Konvert implementation was not found: expected pkg.FooMapperImpl` | `ksp("io.mcarle:konvert:4.5.1")` is missing, or two nested `@Konverter` interfaces share a simple name and collided |
| `Generated class … must have exactly one public constructor` | A `componentModel` / `injectionStrategy` combination emitted several. Simplify, or declare the mapper yourself as a `@Component` |
| Mapper resolves but is never the generated impl | You also declared it in a `@Module`. Extensions are a fallback, so your declaration wins silently. Delete one |
| Phantom `ru.tinkoff.kora` errors from generated mapper code | Stale output under `build/`. `./gradlew clean` and rebuild. Never edit generated sources |
| IDE shows the mapper interface unimplemented | Expected before a build. `./gradlew classes` |
