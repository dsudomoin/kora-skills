# Kora MapStruct

DTO ↔ entity mapping in Kora 2.0. Kora generates no mappers — it ships three **extensions** that
bind an implementation another processor produced, so an annotated mapper interface becomes an
injectable component with no `@Component` and no module to plug into `@KoraApp`.

- **Java → MapStruct.** `org.mapstruct.@Mapper` + the MapStruct javac processor;
  `mapstruct-java-extension` binds the generated `*MapperImpl`.
- **Kotlin → Konvert.** `io.mcarle.konvert.api.@Konverter` + Konvert's KSP processor;
  `konvert-ksp-extension` binds the generated `object <Name>Impl`. **kapt is not the 2.0 path.**
- **Kotlin consuming a Java MapStruct mapper** → `mapstruct-ksp-extension` binds an impl that is
  already on the compile classpath.

All three ship inside `io.koraframework:annotation-processors` / `io.koraframework:symbol-processors`
and are never declared directly. `ru.tinkoff.kora:mapstruct-extension` does not exist in 2.0.

## When to Use

- Mapping between request/response DTOs, domain entities and persistence rows
- Field renames, ignores, computed values, enum ↔ String conversion
- PATCH-style in-place updates via `@MappingTarget` (Java/MapStruct)
- Deciding between MapStruct, Konvert, and a hand-written mapper

## Entry point

Read `SKILL.md` for the Quick Starts and the Kotlin ruling, then the files under `references/`.

## Resources

- `SKILL.md` — mechanism, Java and Kotlin quick starts, versions, decision table, pitfalls
- `references/` — mapper discovery and annotations, build configuration, MapStruct expressions
- `assets/` — Java `@Mapper` and Kotlin `@Konverter` templates, plus Gradle snippets for both
- `evals/` — regression cases covering the removed coordinate, the kapt question, and version pinning
