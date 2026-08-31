# `Lifecycle` Reference — init and release in Kora 2.0

**Kora 2.0** · `io.koraframework.application.graph.{Lifecycle, LifecycleWrapper, Wrapped}`

---

## 1. The contract

```java
package io.koraframework.application.graph;

public interface Lifecycle {
    void init() throws Exception;
    void release() throws Exception;
}
```

That is the whole interface. Both methods return **`void`** and run **synchronously**. Kora 2.0 has
no reactive, `CompletionStage` or `suspend` container contracts, so any 1.x-era signature returning a
publisher or a future is a compile error against this interface — as is a Kotlin `suspend fun init()`.

Kotlin implementations are plain functions; `throws Exception` has no Kotlin counterpart and is
simply omitted:

```kotlin
override fun init() { … }
override fun release() { … }
```

---

## 2. When each method runs

Verified against `GraphImpl` (`core/application-graph`, `TmpGraph.createNode` / `GraphImpl.release`):

**Creation**

1. every node is created on its own virtual thread (`init-node-<index>`), first awaiting the futures
   of its own dependencies and interceptors — so initialisation is as parallel as the graph allows
   while still honouring dependency order;
2. the factory produces the instance;
3. if it implements `Lifecycle`, `init()` is called immediately;
4. each registered `GraphInterceptor.afterInit(value)` is applied, in declaration order;
5. only then is the (possibly replaced) value visible to dependents.

**Release**

1. nodes are released on virtual threads (`release-<index>`) in reverse index order, each holding
   read locks on its dependencies — a component is released only after everything depending on it;
2. each `GraphInterceptor.beforeRelease(value)` is applied in **reverse** interceptor order;
3. `release()` is called if the component implements `Lifecycle`;
4. `close()` is called if the component implements `AutoCloseable` — this happens **in addition to**
   `release()`, and also for components that implement only `AutoCloseable`.

`KoraApplication.run` registers a JVM shutdown hook named `kora-shutdown` that performs the release
and only then lets the JVM exit, so `release()` is what runs on `SIGTERM`.

---

## 3. Error handling

| Situation | Result |
|---|---|
| `init()` throws a runtime exception | that exception aborts graph initialisation |
| `init()` throws a checked exception | wrapped: `IllegalStateException: Lifecycle init failed with checked exception for node <type> at index <n>` |
| any node fails during init | everything already created is released, then the error is rethrown |
| several nodes fail | `IllegalStateException: Application graph failed to initialize with N errors; see suppressed exceptions` |
| `release()` throws | collected and rethrown (further failures attached as suppressed); other nodes still get released |
| `KoraApplication.run` catches an init failure | logs `Application initializing failed with error` and calls `System.exit(-1)` |

`init()` is the right place to fail fast. A component that cannot reach its backing service should
throw rather than start half-configured.

---

## 4. A component with its own lifecycle

```java
package com.example.jobs;

import io.koraframework.application.graph.Lifecycle;
import io.koraframework.common.annotation.Component;
import io.koraframework.common.annotation.Root;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

@Root
@Component
public final class SessionCleaner implements Lifecycle {

    private final SessionRepository repository;
    private final ScheduledExecutorService scheduler;

    public SessionCleaner(SessionRepository repository) {
        this.repository = repository;
        this.scheduler = Executors.newSingleThreadScheduledExecutor();
    }

    @Override
    public void init() {
        scheduler.scheduleWithFixedDelay(this::purge, 1, 5, TimeUnit.MINUTES);
    }

    @Override
    public void release() throws Exception {
        scheduler.shutdown();
        if (!scheduler.awaitTermination(30, TimeUnit.SECONDS)) {
            scheduler.shutdownNow();
        }
    }

    private void purge() {
        repository.deleteExpired();
    }
}
```

Construct in the constructor, **start** in `init()`, **stop** in `release()`. Doing the work in the
constructor defeats the ordering guarantees: at construction time, dependents do not exist yet but
neither do the interceptors that may replace this instance.

> For recurring jobs prefer the scheduling modules
> ([`kora-aop-scheduling-jdk`](../../kora-aop-scheduling-jdk/SKILL.md)) over a hand-rolled executor;
> the example above is about the lifecycle mechanics.

---

## 5. `Wrapped<T>` and `LifecycleWrapper<T>`

```java
public interface Wrapped<T> {
    T value();
    static <T> ValueOf<T> unwrap(ValueOf<Wrapped<T>> valueOf);
}

public class LifecycleWrapper<T> implements Lifecycle, Wrapped<T> {

    @FunctionalInterface
    public interface ThrowingConsumer<T> {
        void accept(T t) throws Exception;
    }

    public LifecycleWrapper(T value, ThrowingConsumer<T> init, ThrowingConsumer<T> release) { … }
}
```

A factory method that returns `Wrapped<T>` registers a component of type **`T`**: the container
unwraps it, so consumers inject `T` and never see the wrapper. This is how you attach a lifecycle to
a type you do not own.

The constructor takes exactly three arguments — value, init consumer, release consumer. There is no
static `wrap(...)` factory, and the consumers are `ThrowingConsumer`, so they may throw checked
exceptions.

**Java**

```java
package com.example.activity;

import io.koraframework.application.graph.LifecycleWrapper;
import io.koraframework.application.graph.Wrapped;
import io.koraframework.common.annotation.Module;

@Module
public interface ActivityModule {

    default Wrapped<ActivityRecorder> activityRecorder(ActivityConfig config) {
        var recorder = new ActivityRecorder(config.endpoint());
        return new LifecycleWrapper<>(recorder, ActivityRecorder::connect, ActivityRecorder::disconnect);
    }
}
```

**Kotlin** — pass the three arguments **positionally**. Kotlin named arguments are not available for
a Java constructor, so `LifecycleWrapper(value, init = …, release = …)` does not compile:

```kotlin
package com.example.activity

import io.koraframework.application.graph.LifecycleWrapper
import io.koraframework.application.graph.Wrapped
import io.koraframework.common.annotation.Module

@Module
interface ActivityModule {

    fun activityRecorder(config: ActivityConfig): Wrapped<ActivityRecorder> {
        val recorder = ActivityRecorder(config.endpoint())
        return LifecycleWrapper(recorder, ActivityRecorder::connect, ActivityRecorder::disconnect)
    }
}
```

Use `r -> {}` (Java) / `{}` (Kotlin) for a hook you do not need — both arguments are mandatory.

### Choosing between the two

| | `implements Lifecycle` | `Wrapped<T>` + `LifecycleWrapper` |
|---|---|---|
| the class is yours | preferred | unnecessary indirection |
| third-party / final / generated type | impossible | the only option |
| several instances with different config | one class, several module methods | natural fit |
| consumers inject | the class itself | the unwrapped `T` |

---

## 6. `AutoCloseable` without `Lifecycle`

A component that implements only `AutoCloseable` still gets `close()` on release. That is enough for
resources with no startup step; reach for `Lifecycle` when there is real init work or when
`release()` must run before `close()`.

---

## 7. Refresh interaction

When the graph is refreshed (`RefreshableGraph.refresh(node)`), affected nodes are rebuilt: the new
instance is created and initialised, and the old one is released afterwards. If the factory returns
an instance `equals` to the previous one, the newly created value is released immediately and the old
node value is kept — so a refresh that changes nothing costs nothing.

A component implementing `RefreshListener` additionally gets `graphRefreshed()` after a refresh
completes; exceptions from it are logged (`Exception caught when calling listener.graphRefreshed()`)
and do not fail the refresh. See
[`runtime-graph-api-reference.md`](runtime-graph-api-reference.md).

---

## 8. Pitfalls

| Symptom | Cause |
|---|---|
| `init()` never runs | component pruned — see [`root-component-reference.md`](root-component-reference.md) |
| `init() … cannot implement init() in Lifecycle` (Java) / `'init' overrides nothing` (Kotlin) | 1.x reactive or `suspend` signature; must be `void init()` |
| Kotlin `suspend fun init()` rejected | no suspend contracts in Kora 2.0 |
| resources leak on shutdown | work done in the constructor, undone nowhere |
| `release()` blocks shutdown | unbounded `awaitTermination`; always bound it |
| `LifecycleWrapper` named arguments do not compile (Kotlin) | Java constructor — pass positionally |
| `incompatible types: LifecycleWrapper<Impl> cannot be converted to Wrapped<Iface>` | `Wrapped<T>` is invariant. Returning `new LifecycleWrapper<>(…)` directly is fine — the diamond infers from the return type — but assigning it to a `var` local first, or writing an explicit `<Impl>` argument, locks in the implementation type |
| factory returns `T` but hooks never fire | the return type must be `Wrapped<T>` |

---

## See also

- [`root-component-reference.md`](root-component-reference.md) — making a component reachable
- [`graph-interceptor-reference.md`](graph-interceptor-reference.md) — `afterInit` / `beforeRelease` around these callbacks
- [`runtime-graph-api-reference.md`](runtime-graph-api-reference.md) — `KoraApplication`, refresh, `RefreshListener`
