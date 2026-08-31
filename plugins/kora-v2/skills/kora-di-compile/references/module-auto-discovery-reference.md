# Module Auto-Discovery Reference

**Applies to:** Kora 2.x (`io.koraframework`)

## Contents

- [Overview](#overview)
- [The Actual Rule](#the-actual-rule)
- [Decision Table](#decision-table)
- [Single-Module Project](#single-module-project)
- [Multi-Module Project](#multi-module-project)
- [Common Mistakes](#common-mistakes)
- [Moving a Module Between Layouts](#moving-a-module-between-layouts)
- [Related References](#related-references)

## Overview

Whether a `@Module` needs to be named in `extends` has nothing to do with packages, directories or
naming. It depends on one thing: **did the annotation processor compile that interface in this
run?**

## The Actual Rule

The `@KoraApp` processor collects every `@Module`-annotated interface it is handed during the
current compilation, and adds all of them to the graph regardless of package. Modules that arrive as
already-compiled bytecode — a Kora artifact, another Gradle subproject, any jar — never pass through
the processor, so they are invisible until the `@KoraApp` interface names them.

Consequences worth internalising:

- Package layout is irrelevant. A `@Module` in a sibling package, a parent package or no shared
  package at all is still discovered, as long as it is in the same compilation.
- **Source set matters.** A `@Module` in `src/test/java` is only discovered when the test
  compilation runs its own processor (`testAnnotationProcessor` / `kspTest`).
- `extends` on a discovered module is harmless — the processor de-duplicates — but it is noise.
- A discovered module's own `extends` chain is followed too, so extending one Kora module pulls in
  everything it inherits.

## Decision Table

| Where the module lives | `extends` on `@KoraApp`? | Why |
|---|---|---|
| `@Module` interface in the same source set | **No** | the processor compiled it |
| `@Module` in `src/test/…` while `@KoraApp` is in `src/main/…` | **No**, but only for a test `@KoraApp` | different compilation |
| Module interface from a Kora artifact (`HoconConfigModule`, `JdbcDatabaseModule`, `UndertowPublicHttpServerModule`, …) | **Yes** | bytecode on the classpath |
| A plain (unannotated) module interface from your own library subproject | **Yes** | bytecode on the classpath |
| `@KoraSubmodule` interface from another Gradle subproject | **Yes** | see [@KoraSubmodule Reference](kora-submodule-reference.md) |

Note the fourth row: a module interface shipped by a library does **not** need `@Module` at all.
`@Module` exists purely to make discovery work; an interface that is always named in `extends` works
without it. Kora's own `EmailModule`-style library modules in the guides are plain interfaces.

## Single-Module Project

```
my-app/
├── build.gradle
└── src/main/java/com/example/
    ├── Application.java        // @KoraApp
    ├── storage/StorageModule.java   // @Module — discovered
    └── activity/ActivityModule.java // @Module — discovered
```

```java
// Application.java
@KoraApp
public interface Application extends HoconConfigModule, LogbackModule {
    // StorageModule and ActivityModule are already in the graph
    static void main(String[] args) { KoraApplication.run(ApplicationGraph::graph); }
}
```

```java
// storage/StorageModule.java — no extends needed anywhere
@Module
public interface StorageModule {
    default Storage storage(StorageConfig config) { return new TempFileStorage(config); }
}
```

## Multi-Module Project

```
my-app/
├── common/  src/main/java/com/example/common/CommonModule.java   // @KoraSubmodule
├── pet-api/ src/main/java/com/example/pet/PetModule.java         // @KoraSubmodule
└── app/     src/main/java/com/example/app/Application.java       // @KoraApp
```

```java
@KoraApp
public interface Application extends
        PetModule,          // @KoraSubmodule from pet-api
        HoconConfigModule,  // io.koraframework:config-hocon
        LogbackModule {     // io.koraframework:logging-logback

    static void main(String[] args) { KoraApplication.run(ApplicationGraph::graph); }
}
```

`PetModule` itself extends `CommonModule`, so `CommonModule` need not be repeated.

## Common Mistakes

### Assuming a cross-subproject `@Module` is discovered

```java
// BAD — pet-api is compiled separately; @Module alone does nothing across a Gradle boundary
// pet-api: @Module public interface PetModule { … }
@KoraApp
public interface Application extends HoconConfigModule { }   // PetModule missing from the graph

// GOOD — mark it @KoraSubmodule and extend it
// pet-api: @KoraSubmodule public interface PetModule { … }
@KoraApp
public interface Application extends PetModule, HoconConfigModule { }
```

Symptom: `No component found for dependency` for something `PetModule` clearly provides.

### Forgetting an external module

```java
// BAD — no config module, so nothing provides Config
@KoraApp
public interface Application { }

// GOOD
@KoraApp
public interface Application extends HoconConfigModule { }
```

The error names the missing type and, under `Hint:`, usually names the module that provides it.

### Expecting discovery without the processor

A `@Module` in a Gradle module that has no `annotation-processors` (Java) or `symbol-processors`
(KSP) dependency is never seen. Every module that declares Kora annotations needs the processor on
its own compile classpath.

### Discovery in tests

```groovy
// A test-only @Module or a test @KoraApp needs its own processor wiring
testAnnotationProcessor "io.koraframework:annotation-processors"
```

```kotlin
kspTest("io.koraframework:symbol-processors:${property("koraVersion")}")
```

## Moving a Module Between Layouts

### Same compilation → separate Gradle subproject

```java
// Before — discovered automatically
@Module
public interface DatabaseModule { }

// After — moved to a database-api subproject
@KoraSubmodule
public interface DatabaseModule { }

// and named explicitly by the app
@KoraApp
public interface Application extends DatabaseModule, HoconConfigModule { }
```

The subproject also needs the annotation processor, and the app needs `implementation project(...)`
on it.

### Separate Gradle subproject → same compilation

Swap `@KoraSubmodule` back to `@Module`, drop it from the `extends` list, and remove the
now-unnecessary project dependency.

## Related References

- [@KoraApp Reference](kora-app-component-reference.md) — bootstrap and the generated graph
- [Component Registration Reference](component-registration-reference.md) — how types enter the graph
- [@KoraSubmodule Reference](kora-submodule-reference.md) — multi-module Gradle builds
