---
name: kora-mapstruct
description: "DTO ↔ entity mapping in Kora 2.0. Java: org.mapstruct @Mapper + the MapStruct javac processor. Kotlin: Konvert @Konverter + its KSP processor — kapt is not the 2.0 path. The matching extension ships inside annotation-processors / symbol-processors, finds the generated *Impl and binds it into the graph with no @Component. Use for @Mapper, @Konverter, @Mapping, mapstruct-processor wiring, and the removed mapstruct-extension coordinate."
license: Apache-2.0
metadata:
  kora-version: "2.x"
---

# kora-mapstruct — MapStruct (Java) and Konvert (Kotlin) mappers as Kora components

> **Kora sub-skill — obey the [kora-v2 meta rules](../../SKILL.md) on every task:** **R0** ground the workspace on Kora 2.0 refs before starting (framework source at tag `2.0.0.RC1` + `kora-examples` at `migration/2.0`; `kora-docs` is 1.x only) · **R1** read this sub-skill before writing code · **R2** Kora 2.0 APIs only — no Spring/Micronaut/Quarkus, no Kora 1.x APIs, no invented annotations or config keys · **R3** journal any incorrect Kora usage. Add comments/Javadoc only if asked.

Kora does not generate mappers. It ships three **extensions** that bind an implementation some
*other* processor generated, so an annotated mapper interface can be injected like any component.

| Extension module | Runs in | Recognises | Binds |
|---|---|---|---|
| `mapping/mapstruct-java-extension` | javac annotation processing | `org.mapstruct.@Mapper` on an interface **or class** | the single public constructor of the MapStruct-generated `<Name>Impl` |
| `mapping/mapstruct-ksp-extension` | KSP | `org.mapstruct.@Mapper` on an interface **or class** | the constructor of `<Name>Impl` — **only if it is already on the classpath**; KSP cannot run MapStruct |
| `mapping/konvert-ksp-extension` | KSP | `io.mcarle.konvert.api.@Konverter` on an **interface** | the Konvert-generated `object <Name>Impl` as a dependency-free singleton |

**Never `@Component` the mapper.** The extension supplies it, and it is a *last-resort fallback*:
`GraphBuilder` consults extensions only after declared components and `@Module` methods fail to match
the dependency claim. `@Component` on a mapper is not an error — it is `@Target(TYPE)`, so it
compiles, and `KoraAppProcessor.processComponents` then silently skips it because a MapStruct mapper
is an interface or an abstract class and the loop skips both. It does nothing at all. Delete it: it
reads as wiring that isn't there.

## The artifacts

There is **no `mapstruct-extension` in Kora 2.0.** That single 1.x coordinate split into a javac
half and a KSP half, and `konvert-ksp-extension` is new in 2.0 alongside them.

You do not declare the extensions either — the aggregate processors already contain them:

- `io.koraframework:annotation-processors` → `mapstruct-java-extension`
- `io.koraframework:symbol-processors` → `mapstruct-ksp-extension` **and** `konvert-ksp-extension`

What you *do* declare is the third-party half: the mapping library on the **compile** classpath plus
its processor. The compile-classpath part is not optional — each extension factory looks the
annotation type up (`org.mapstruct.Mapper` / `io.mcarle.konvert.api.Konverter`) and disables itself
when it is absent, silently leaving you with "no component found for `FooMapper`".

## Quick Start (Java) — MapStruct

`gradle.properties`:

```properties
koraVersion=2.0.0.RC1
```

`build.gradle`:

```groovy
repositories { mavenCentral() }

configurations {
    koraBom
    annotationProcessor.extendsFrom(koraBom); compileOnly.extendsFrom(koraBom)
    implementation.extendsFrom(koraBom); api.extendsFrom(koraBom)
    testImplementation.extendsFrom(koraBom); testAnnotationProcessor.extendsFrom(koraBom)
}

dependencies {
    koraBom platform("io.koraframework:kora-bom:$koraVersion")

    annotationProcessor "io.koraframework:annotation-processors"          // carries mapstruct-java-extension
    annotationProcessor "org.mapstruct:mapstruct-processor:1.6.3"         // generates *MapperImpl
    implementation "org.mapstruct:mapstruct:1.6.3"                        // @Mapper on the compile classpath
}
```

`kora-bom` constrains only `io.koraframework:*`. **MapStruct is versioned by you**, and the API and
the processor must carry the *same* version.

```java
import org.mapstruct.Mapper;
import org.mapstruct.Mapping;
import org.mapstruct.ReportingPolicy;

@Mapper(unmappedTargetPolicy = ReportingPolicy.IGNORE)   // no @Component
public interface CarMapper {

    @Mapping(source = "numberOfSeats", target = "seatCount")
    CarTO map(Car car);
}
```

```java
import io.koraframework.common.annotation.Component;

@Component
public final class CarService {
    private final CarMapper mapper;

    public CarService(CarMapper mapper) {
        this.mapper = mapper;
    }
}
```

**Processor order in the `dependencies` block does not matter.** `KoraAppProcessor` builds the graph
only when `roundEnv.processingOver()` is true — the last annotation-processing round — by which point
every `*MapperImpl` from earlier rounds is already in the element table. Kora's own extension test
registers `KoraAppProcessor` *before* MapStruct's `MappingProcessor` and passes. The
`kora-java-crud` example happens to list `mapstruct-processor` first; that is convention, not a
requirement.

## Quick Start (Kotlin) — Konvert

`build.gradle.kts`:

```kotlin
plugins {
    kotlin("jvm") version "2.4.10"
    id("com.google.devtools.ksp") version "2.3.11"
}

repositories { mavenCentral() }

dependencies {
    implementation(platform("io.koraframework:kora-bom:${property("koraVersion")}"))

    ksp("io.koraframework:symbol-processors:${property("koraVersion")}")  // carries konvert-ksp-extension
    ksp("io.mcarle:konvert:4.5.1")                                        // generates object <Name>Impl
    implementation("io.mcarle:konvert-api:4.5.1")                         // @Konverter on the compile classpath
}
```

```kotlin
import io.mcarle.konvert.api.Konverter

@Konverter                                   // interface only; no @Component
interface PetMapper {
    fun petWithCategoryToPetTO(pet: PetWithCategory): PetTO
    fun petCategoryToCategoryTO(category: PetCategory): CategoryTO
}
```

This is exactly what `kora-examples`' `kora-kotlin-crud` does on `migration/2.0`.

## Kotlin: kapt is **not** the Kora 2.0 path

The 1.x advice — "MapStruct on Kotlin needs kapt alongside KSP, pin Kotlin 1.9.10" — is dead. It is
not merely awkward under Kotlin 2.4.10; it was deliberately removed from the corpus. Evidence:

1. **The 2.0 migration commit deleted it.** In `kora-examples`, commit *"Migrate all modules to Kora
   2.0 (#49)"* removed every `kapt(...)`, `kaptKotlin`/`kaptGenerateStubsKotlin` `dependsOn`, and
   `build/generated/source/kapt/main` `srcDir` line from `kora-kotlin-crud/build.gradle.kts`. Each
   carried the comment `// KAPT & KSP broken since 1.9.11`.
2. **`kapt` occurs zero times** anywhere in the migrated corpus — examples, guides and migration
   scripts alike.
3. **Konvert replaced it.** The follow-up commit added `ksp("io.mcarle:konvert")` +
   `implementation("io.mcarle:konvert-api")` and rewrote `PetMapper.kt` as a `@Konverter` interface.
4. **KSP structurally cannot run MapStruct.** MapStruct ships only a javac processor
   (`org.mapstruct.ap.MappingProcessor` — the class Kora's *Java* extension test instantiates), and
   `mapstruct-ksp-extension` contains no code generator at all: it resolves `<Name>Impl` through
   `resolver.getClassDeclarationByName` and binds its constructor. Its own test never runs MapStruct
   — it hand-writes `CarMapperImpl.kt` in the test sources. So a `@Mapper` declared in **Kotlin**
   source has no impl generator in a KSP-only build.

**So what is `mapstruct-ksp-extension` for?** For a Kotlin `@KoraApp` that injects a MapStruct mapper
whose `*MapperImpl` **already exists on the compile classpath** — the normal case being a mapper
declared in a Java module (or a published jar) where javac + `mapstruct-processor` produced the impl.
It lets a Kotlin graph consume a Java-side MapStruct mapper. It does not make MapStruct work on
Kotlin sources.

**Rule:** map in Kotlin → Konvert. Need MapStruct specifically → declare the mapper in a Java module.

## MapStruct version: `1.6.3` vs `1.5.5.Final` — both are real, pick `1.6.3`

| Source | Version |
|---|---|
| Kora's version catalog (`libs.mapstruct` / `libs.mapstruct.processor`, used to compile and test both MapStruct extensions) | **`1.6.3`** |
| `kora-examples` on `migration/2.0` — all four Java examples | `1.5.5.Final` |

They genuinely disagree. `1.5.5.Final` entered those builds long before the 2.0 work (commit
*"Refactored structure and more Kotlin examples (#42)"*) and the migration commit never touched the
line — it rewrote Kora coordinates only. **Recommend `1.6.3`**: it is the version Kora's own
extension is compiled and tested against. Whichever you pick, `org.mapstruct:mapstruct` and
`org.mapstruct:mapstruct-processor` must match; a split pin fails at generation time with
MapStruct's own errors, which mention nothing about Kora.

Konvert has no such disagreement: Kora's catalog pins `io.mcarle:konvert-api` at **`4.5.1`**, and
`kora-kotlin-crud` pins both `konvert-api` and the `io.mcarle:konvert` processor at `4.5.1`.

## Decision: which tool

| Situation | Use |
|---|---|
| Java module, 5+ DTO/entity pairs with high field overlap | MapStruct |
| Kotlin module | Konvert |
| Kotlin module that must use MapStruct | Declare the mapper in a Java module; inject it via `mapstruct-ksp-extension` |
| 1–3 mappers, or significant per-field logic | Hand-written mapping — no processor, no extension |

## Mapper contracts in 2.0

- Mapper methods are **ordinary synchronous methods**. Never `Mono`/`Flux`/`CompletionStage`, never
  `suspend` — those are not Kora 2.0 contracts.
- Java nullability is **JSpecify** (`org.jspecify.annotations.Nullable`), and it is *type-use*:
  `List<@Nullable String>`, `Outer.@Nullable Inner`. Kotlin nullability is the type (`T?`).
- Crossing the boundary: a generated Java `*MapperImpl` method is a *platform type* to a Kotlin
  caller. Annotate the Java mapper interface (`@NullMarked` / `@Nullable`) rather than forcing `!!`.

---

## What's in `references/`

| Document | Purpose |
|----------|---------|
| [`mapstruct-mapper-reference.md`](references/mapstruct-mapper-reference.md) | Verified discovery rules, generated-impl naming, `@Tag`, mappers with dependencies, `@Mapper`/`@Mapping`/`@MappingTarget`/`@Named` |
| [`mapstruct-config-reference.md`](references/mapstruct-config-reference.md) | Java (MapStruct) and Kotlin (Konvert) build wiring, versions, `componentModel`, troubleshooting the real 2.0 errors |
| [`mapstruct-expressions-reference.md`](references/mapstruct-expressions-reference.md) | MapStruct `expression`, `defaultValue`, `constant`, `nullValuePropertyMappingStrategy` |

## What's in `assets/`

- `OrderMapper.java.template` — Java `@Mapper` with renames, ignores, expressions, PATCH update
- `OrderMapper.kt.template` — Kotlin `@Konverter` equivalent, with the shapes Konvert cannot express
- `mapstruct.gradle.snippet` — Java (Groovy DSL) MapStruct wiring
- `mapstruct.gradle.kts.snippet` — Kotlin (Kotlin DSL) Konvert wiring, plus the Java-module MapStruct route

---

## Common pitfalls

| Symptom | Cause / fix |
|---|---|
| `io.koraframework:mapstruct-extension` does not resolve | It does not exist in 2.0. Use nothing — `annotation-processors` / `symbol-processors` already carry the right extension |
| No component found for `FooMapper` | The mapping library is missing from the **compile** classpath, so the extension factory disabled itself. Add `implementation "org.mapstruct:mapstruct:…"` / `implementation("io.mcarle:konvert-api:…")` |
| `MapStruct mapper implementation was not generated for FooMapper` | Kora found `@Mapper` but no `FooMapperImpl`. The MapStruct processor is not on `annotationProcessor`, or MapStruct itself errored earlier in the same compile |
| Same error in a **Kotlin** module | Expected — KSP does not run MapStruct. Switch to Konvert, or move the mapper to a Java module |
| `Generated class FooMapperImpl must have exactly one public constructor` | A `componentModel` / `injectionStrategy` combination produced several. Pin one strategy, or provide the mapper as a plain `@Component` yourself |
| `@Component` on the mapper changes nothing | Correct — it is silently skipped (interface / abstract class). The extension is what registers the mapper; remove the annotation |
| A hand-written `@Module` method shadows the mapper | Extensions are the fallback, so your declaration silently wins and the generated `*Impl` is never used. Delete one of the two |
| Two Konvert mappers with the same simple name in one package | Konvert emits a **top-level** `object <SimpleName>Impl`, dropping any enclosing type name — the two collide. Rename one |
| Stale `ru.tinkoff.kora` errors from generated mapper code | Old output under `build/`. `./gradlew clean` and rebuild; never edit generated sources |

---

## Upstream references

- Kora extensions: <https://github.com/kora-projects/kora/tree/2.0.0.RC1/mapping>
- Working Java example: <https://github.com/kora-projects/kora-examples/tree/migration/2.0/examples/java/kora-java-crud>
- Working Kotlin (Konvert) example: <https://github.com/kora-projects/kora-examples/tree/migration/2.0/examples/kotlin/kora-kotlin-crud>
- MapStruct: <https://mapstruct.org/documentation/stable/reference/html/>
- Konvert: <https://mcarleio.github.io/konvert/>
