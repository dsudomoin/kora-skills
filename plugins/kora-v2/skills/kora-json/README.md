# Kora JSON

Compile-time JSON for Kora 2.x: `@Json` DTOs, sealed hierarchies, enums, custom mappers.

## When to use

- DTO serialization/deserialization for HTTP bodies, Kafka payloads, cache values
- Polymorphic JSON over sealed interfaces / sealed abstract classes
- Enum serialization (default `toString()`, or a `@Json`-annotated value accessor)
- Custom `JsonReader`/`JsonWriter` components, or per-field `@Mapping`
- Routing HTTP JSON bodies through a Jackson 3 `ObjectMapper`

## Quick start

See [SKILL.md](SKILL.md) for the Gradle dependencies, module wiring, and a runnable
controller. In short: add `io.koraframework:annotation-processors` (Kotlin:
`ksp "io.koraframework:symbol-processors"`) plus `io.koraframework:json-common`, extend
`io.koraframework.json.common.JsonModule` on the `@KoraApp` interface, and annotate DTO
records/data classes with `@Json`.

## Key features

- `@Json`, `@JsonReader`, `@JsonWriter`, `@JsonField`, `@JsonSkip`, `@JsonInclude`
- `@JsonDiscriminatorField` / `@JsonDiscriminatorValue` for sealed hierarchies
- `JsonNullable<T>` — missing vs explicit null (PATCH)
- `RawJson` — pre-encoded JSON passed through untouched
- `@NamingStrategy` for wholesale field renaming
- Custom mappers as DI components, or per-field via `@Mapping`

## Triggers

`@Json`, `@JsonField`, `json-common`, `JsonModule`, `JsonNullable`, sealed interface JSON,
enum serialization, custom mapper, `JsonReader not found`, `JacksonModule`

## Resources

- **SKILL.md** — full documentation and the 1.x → 2.x delta
- **references/** — DTOs, sealed types, custom mappers, configuration, best practices
- **assets/** — Java and Kotlin templates: DTO, enum, sealed interface (+ impl), custom mapper
- **evals/** — behavioural evals, including 2.0 regression cases
