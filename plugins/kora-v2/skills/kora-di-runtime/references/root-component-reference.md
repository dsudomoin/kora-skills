# `@Root` Reference — reachability in the Kora 2.0 graph

**Kora 2.0** · `io.koraframework.common.annotation.Root`

---

## 1. The annotation

```java
package io.koraframework.common.annotation;

@Target({ElementType.TYPE, ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Root {
}
```

It takes no attributes. It is legal on:

- a `@Component` class,
- a factory method of a `@KoraApp` interface,
- a factory method of a `@Module` / `@KoraSubmodule` interface.

It is **not** legal on a constructor, a field or a parameter.

---

## 2. Why a component disappears without it

Kora builds the container at compile time by graph traversal, not by scanning the classpath. The
annotation processor collects a **root set** — every declaration annotated `@Root` — pushes it onto
the resolution stack, and resolves dependencies transitively from there. A declaration the traversal
never reaches produces no node, no factory call and no `Lifecycle` callback.

Consequences worth stating explicitly:

- **Implementing `Lifecycle` does not make a component reachable.** Reachability and lifecycle are
  independent concerns. A component whose entire purpose is a side effect in `init()` has, by
  definition, no dependents — so it is exactly the component that gets pruned.
- The failure is silent. Compilation succeeds, the graph initialises, the application runs, and the
  work simply never happened.
- Adding a dependent is an equally valid fix. `@Root` is the way to say "this is a top-level entry
  point"; being someone's dependency is the other way in.

With no `@Root` anywhere in the module set the build fails instead:

```
@KoraApp has no root components.

Fix:
  - Check that modules with @Root components are plugged-in.
  - Annotate at least one component or module method with @Root.
  - Check that root component is visible from this @KoraApp module set.
```

---

## 3. The canonical case — a bucket initialiser

Bucket administration is not part of the declarative `@S3` contract, so it runs through the AWS SDK
client. Nothing in the application depends on the initialiser, therefore it must be a root.

```java
package com.example.s3;

import io.koraframework.application.graph.Lifecycle;
import io.koraframework.common.annotation.Component;
import io.koraframework.common.annotation.Root;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.CreateBucketRequest;
import software.amazon.awssdk.services.s3.model.HeadBucketRequest;
import software.amazon.awssdk.services.s3.model.NoSuchBucketException;

@Root
@Component
public final class S3BucketInitializer implements Lifecycle {

    private final S3Client s3Client;
    private final UploadsConfig config;

    public S3BucketInitializer(S3Client s3Client, UploadsConfig config) {
        this.s3Client = s3Client;
        this.config = config;
    }

    @Override
    public void init() {
        var bucket = this.config.bucket();
        try {
            this.s3Client.headBucket(HeadBucketRequest.builder().bucket(bucket).build());
        } catch (NoSuchBucketException e) {
            this.s3Client.createBucket(CreateBucketRequest.builder().bucket(bucket).build());
        }
    }

    @Override
    public void release() {}
}
```

Kotlin:

```kotlin
package com.example.s3

import io.koraframework.application.graph.Lifecycle
import io.koraframework.common.annotation.Component
import io.koraframework.common.annotation.Root
import software.amazon.awssdk.services.s3.S3Client
import software.amazon.awssdk.services.s3.model.CreateBucketRequest
import software.amazon.awssdk.services.s3.model.HeadBucketRequest
import software.amazon.awssdk.services.s3.model.NoSuchBucketException

@Root
@Component
class S3BucketInitializer(
    private val s3Client: S3Client,
    private val config: UploadsConfig
) : Lifecycle {

    override fun init() {
        val bucket = config.bucket()
        try {
            s3Client.headBucket(HeadBucketRequest.builder().bucket(bucket).build())
        } catch (e: NoSuchBucketException) {
            s3Client.createBucket(CreateBucketRequest.builder().bucket(bucket).build())
        }
    }

    override fun release() {}
}
```

Delete `@Root` from either version and the bucket is never created — with no error anywhere.

---

## 4. `@Root` on a factory method

When the type is not yours to annotate, put `@Root` on the method that provides it.

```java
@Module
public interface WarmupModule {

    @Root
    default Wrapped<CacheWarmer> cacheWarmer(UserRepository repository) {
        var warmer = new CacheWarmer(repository);
        return new LifecycleWrapper<>(warmer, CacheWarmer::warmup, w -> {});
    }
}
```

The same works on a `@KoraApp` interface method. `@Root` and `@Tag` compose: a root may be tagged
like any other component.

---

## 5. What needs `@Root` in practice

| Component | Needs `@Root`? | Why |
|---|---|---|
| Kora's own HTTP server, Kafka consumers, schedulers | no | their modules already declare roots |
| A `@HttpController` | no | reachable through the generated server wiring |
| Your own socket/listener started by hand | **yes** | nothing injects it |
| A migration / bootstrap / warm-up task | **yes** | pure side effect, no dependents |
| A bucket / topic / index initialiser | **yes** | see §3 |
| A `@Component` service injected by a controller | no | reachable through the controller |
| A `GraphInterceptor<T>` | no | pulled in automatically with the component it intercepts |

Do not sprinkle `@Root` defensively. Every root is an extra entry point that keeps its whole
dependency subtree alive, including in tests.

---

## 6. Diagnosing a pruned component

1. Does anything actually depend on it? If not, and it has no `@Root`, that is the answer.
2. Is the module that declares it plugged into the `@KoraApp` interface? A `@Root` inside a module
   nobody extends is not in the module set.
3. Is the class annotated `@Component`, or provided by a module method? A plain class that is neither
   is not a declaration at all.
4. Enable `DEBUG` for the `@KoraApp` root class — `GraphImpl` logs `Creating node …` /
   `Created node …` per node at `TRACE`, and slow node initialisation at `DEBUG`
   (threshold via the `kora.graph.slowNodeInitThresholdMillis` system property, default `100`).

---

## See also

- [`lifecycle-reference.md`](lifecycle-reference.md) — what `init()` / `release()` guarantee
- [`conditional-graph-evaluation-reference.md`](conditional-graph-evaluation-reference.md) — a root that is skipped by a `GraphCondition`
- [`kora-di-compile`](../../kora-di-compile/SKILL.md) — declaring components and modules
