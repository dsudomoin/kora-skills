# Coroutines in Kora 2.x tests — what replaces `runTest` / `coEvery`

Verified against the Kora 2.0 sources (no `suspend` appears in any Kora contract; the whole framework
API is synchronous) and against every migrated Kotlin example and guide under
[`kora-examples@migration/2.0`](https://github.com/kora-projects/kora-examples/tree/migration/2.0):
**`suspend fun` occurs zero times** in `examples/kotlin` and `guides/kotlin`, and no Kotlin module
declares a `kotlinx-coroutines-*` dependency.

This file exists because 1.x Kotlin test suites are full of `runTest`, `coEvery` and `Dispatchers.IO`
wrapped around Kora calls that no longer suspend. It says what each construct becomes, and what has
no 2.0 equivalent at all.

## Contents

- [The rule](#the-rule)
- [Mechanical replacements](#mechanical-replacements)
- [Scenarios with no 2.0 equivalent](#scenarios-with-no-20-equivalent)
- [Waiting for work on another thread](#waiting-for-work-on-another-thread)
- [Real parallelism: StructuredTaskScope](#real-parallelism-structuredtaskscope)
- [When kotlinx-coroutines-test is still legitimate](#when-kotlinx-coroutines-test-is-still-legitimate)
- [Removing the dependency](#removing-the-dependency)

---

## The rule

Kora 2.0 contracts are **synchronous**, executed on virtual threads. `@Repository` methods,
`@HttpController` handlers, `@HttpClient` methods, `@KafkaListener` methods, cache and resilience
aspects — none of them is `suspend`, and Reactor `Mono`/`Flux` and `CompletionStage` are gone as
well.

Therefore, in a `@KoraAppTest`:

- there is nothing to `await`, so `runTest { }` has no suspension point to drive;
- MockK's `co*` DSL exists for `suspend` functions, and nothing under test suspends any more, so
  `coEvery`/`coVerify` become `every`/`verify`;
- `Dispatchers.IO` around a Kora call is pure overhead — the call already runs on a virtual thread
  that unmounts on blocking I/O;
- `Context` is gone from the entire framework, so nothing has to be propagated across a dispatcher
  boundary.

**The compiler will not find these for you.** `coEvery { }` takes a `suspend` lambda, and a `suspend`
lambda may call ordinary blocking functions, so `coEvery { repo.findById("1") } returns pet` still
compiles unchanged after the repository stops suspending — no warning, no error. The same holds for
`coVerify` and for `runTest { }` wrapped around a body that no longer suspends. Grep for
`coEvery`/`coVerify`/`runTest` explicitly during a migration instead of waiting for a build failure.

---

## Mechanical replacements

| 1.x | 2.0 |
|---|---|
| `@Test fun t() = runTest { … }` | `@Test fun t() { … }` |
| `coEvery { repo.save(any()) } returns "1"` | `every { repo.save(any()) } returns "1"` |
| `coVerify { repo.save(any()) }` | `verify { repo.save(any()) }` |
| `coVerify(exactly = 1) { … }` | `verify(exactly = 1) { … }` |
| `coVerifyOrder { … }` | `verifyOrder { … }` |
| `coEvery { … } coAnswers { … }` | `every { … } answers { … }` |
| `coEvery { … } throws E()` | `every { … } throws E()` |
| `withContext(Dispatchers.IO) { repo.find(id) }` | `repo.find(id)` |
| `runBlocking { service.doWork() }` | `service.doWork()` |
| `assertFailsWith<E> { service.suspendCall() }` inside `runTest` | `assertThrows<E> { service.call() }` |
| `service.stream().toList()` on a `Flow` repository | a `List<T>`-returning `@Query` |

Before and after, same assertion:

```kotlin
// 1.x
@KoraAppTest(Application::class)
class UserServiceCoroutineTest {
    @MockK @TestComponent lateinit var userRepository: UserRepository
    @TestComponent lateinit var userService: UserService

    @Test
    fun createUser() = runTest {
        coEvery { userRepository.save(any()) } returns "1"
        val result = userService.createUser(UserRequest("John", "john@example.com"))
        assertNotNull(result)
        coVerify { userRepository.save(any()) }
    }
}
```

```kotlin
// 2.0
@KoraAppTest(Application::class)
class UserServiceTest {
    @field:MockK @TestComponent lateinit var userRepository: UserRepository
    @TestComponent lateinit var userService: UserService

    @Test
    fun createUser() {
        every { userRepository.save(any()) } returns "1"
        val result = userService.createUser(UserRequest("John", "john@example.com"))
        assertNotNull(result)
        verify { userRepository.save(any()) }
    }
}
```

The test gets shorter and keeps every assertion. That is the expected shape of this migration: if
removing `runTest` also removed an assertion, something was lost.

---

## Scenarios with no 2.0 equivalent

These are **deleted, not weakened**. Say so explicitly in the migration commit rather than leaving a
hollow test behind.

| 1.x test scenario | Why there is no 2.0 version |
|---|---|
| Cancelling a coroutine that is inside a repository call, asserting the query was cancelled | Repository methods are ordinary blocking calls. There is no cancellable suspend contract, and interrupting a virtual thread is not part of any Kora API. |
| `withTimeout(…) { repo.slowQuery() }` asserting the coroutine timeout fires | Same. A per-operation timeout is now a resilience concern — declare a `@Timeout(Spec::class)` aspect and assert on `TimeoutExhaustedException` instead. |
| Collecting a `Flow<T>` returned by a `@Repository`, asserting backpressure or partial consumption | Repositories return `List<T>`/`T`/`UpdateCount`. There is no streaming repository contract to collect. |
| `TestDispatcher` + `advanceTimeBy` to fast-forward a Kora call | Virtual time only advances coroutine delays. A blocking Kora call ignores the `TestScheduler` entirely, so the test would hang or pass for the wrong reason. |
| Asserting `Context` propagation across `withContext` | `Context` no longer exists anywhere in the framework. |
| `runTest` around a `suspend` generated OpenAPI client | Only `java-client`, `java-server`, `kotlin-client`, `kotlin-server` generator modes remain; `kotlin-suspend-*` is gone. |

Where the *intent* survives (a timeout, a retry, a partial-failure policy), re-express it with the 2.0
mechanism and keep the assertion — a `@Timeout`/`@Retryable` aspect with a `resilient` config block in
`KoraConfigModification.ofString`, asserted through the exception the aspect throws.

---

## Waiting for work on another thread

When a component hands work to an executor and the test has to wait for it, there is no scheduler to
advance. Use a real wait with a bound:

```kotlin
// MockK's own timeout
verify(timeout = 5_000) { repository.save(any()) }
```

```kotlin
// Awaitility — the shape the migrated Kafka examples use
Awaitility.await()
    .atMost(Duration.ofSeconds(15))
    .pollExecutorService(Executors.newSingleThreadExecutor())
    .until { consumer.received().size == 1 }
```

Never `Thread.sleep`.

---

## Real parallelism: `StructuredTaskScope`

Kotlin structured concurrency (`coroutineScope`, `async`/`awaitAll`, `supervisorScope`) has no Kora
replacement; the migration target is Java `StructuredTaskScope`. Two things matter for tests:

1. **It is a preview API.** `--enable-preview` must be set on the Kotlin/Java compilation *and* on
   every JVM launch — including `tasks.withType<Test>()`. A build that compiles but forgets the test
   JVM fails at class load with `UnsupportedClassVersionError`-style preview errors.
2. **The API shape changed between JDK 21 and the current preview** (factories, `Joiner`, the return
   type of `join()`). Read the Javadoc of the exact JDK the project targets; do not copy a snippet
   from an older one. JDK 25 is the hard floor for Kora 2.0 artifacts.

No migrated example in `kora-examples@migration/2.0` uses `StructuredTaskScope`, so this package has
no verified template for it. Test such code the ordinary way — call the method, assert on the
aggregate result, and write **new** tests for cancellation and exception propagation, because the
1.x coroutine ones do not carry over.

---

## When `kotlinx-coroutines-test` is still legitimate

Kora does not forbid coroutines in code that is not a Kora contract. If a class of your own is still
`suspend` — a poller, an in-memory pipeline, a client for something outside Kora — test it with the
standard toolkit (`runTest`, `StandardTestDispatcher`/`UnconfinedTestDispatcher`, `advanceUntilIdle`,
`advanceTimeBy`, `currentTime`) exactly as before.

Two constraints when such a test also touches a `@KoraAppTest` graph:

- Do not put a Kora call inside a virtual-time block and expect the clock to skip it. `runTest`
  advances *coroutine* delays; a blocking JDBC or HTTP call takes real wall-clock time.
- Keep the Kora boundary synchronous. Stub the injected Kora component with `every`/`verify`; use
  `coEvery`/`coVerify` only for your own `suspend` functions.

---

## Removing the dependency

Drop `kotlinx-coroutines-core` / `-jdk8` / `-test` only after a repository-wide audit finds no
remaining usages — the migration guide is explicit about that ordering. A leftover
`kotlinx-coroutines-test` on the test classpath is harmless but keeps `runTest` autocompleting,
which is how these constructs creep back into a synchronous codebase.
