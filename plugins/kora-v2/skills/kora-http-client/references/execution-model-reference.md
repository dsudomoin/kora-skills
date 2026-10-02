# HTTP Client Execution Model Reference (Kora 2.x)

Reference for [kora-http-client](../SKILL.md). Covers what replaced the Kora 1.x asynchronous
client contracts.

## Contents

- [The model](#the-model)
- [What was removed](#what-was-removed)
- [CompletionStage and Mono](#completionstage-and-mono)
- [Kotlin suspend](#kotlin-suspend)
- [Migration table](#migration-table)
- [Parallel calls](#parallel-calls)
- [Timeouts instead of composition](#timeouts-instead-of-composition)
- [Tests](#tests)

---

## The model

Every generated `@HttpClient` method is **synchronous**: it builds the request, calls
`HttpClient.execute(request)`, maps the response inside a try-with-resources block and returns the
value. The base contract itself is synchronous —

```java
public interface HttpClient {
    HttpClientResponse execute(HttpClientRequest request) throws HttpClientException;
}
```

— and every transport implements it by blocking the calling thread (`okhttp3.Call.execute()`,
`java.net.http.HttpClient.send`, Apache's classic `HttpClient`). Under Kora 2.0 that caller is
normally a virtual thread: an `@HttpController` handler, a Kafka listener, a scheduled task. The
carrier thread is released while the call is in flight, so there is nothing to gain from wrapping
the call in a future, a `Mono` or `Dispatchers.IO`.

Concurrency is therefore expressed by **running work on more virtual threads**, not by returning a
composable type from the client.

---

## What was removed

| Kora 1.x | Kora 2.0 |
|---|---|
| `CompletionStage<T>` client methods | not a supported contract |
| `Mono<T>` / `Flux<T>` client methods (`reactor-core`) | not a supported contract |
| Kotlin `suspend fun` client methods | hard KSP error |
| `HttpClientInterceptor.processRequest` returning `CompletionStage` | returns `HttpClientResponse` |
| Kora `Context` parameter on interceptors | `Context` was removed from the whole framework |
| artifact `http-client-async` (`AsyncHttpClientModule`) | does not exist — see [transports-reference](transports-reference.md#removed-http-client-async) |

After the rewrite, `io.projectreactor:reactor-core` and
`org.jetbrains.kotlinx:kotlinx-coroutines-core`/`-jdk8` are usually no longer needed by the module.
Remove them only once a repository-wide search finds no remaining usages.

---

## CompletionStage and Mono

These fail **late and confusingly** rather than at the annotation. The processor emits a warning:

```
warning: Method has async signature, this might not work correctly
```

and then generates the same blocking body, asking the graph for a mapper of the wrapper type:

```
No component found for dependency: HttpClientResponseMapper<java.util.concurrent.CompletionStage<ItemResponse>> (no tags)
```

There is no such mapper — `HttpClientResponseMapperModule` has nothing for `CompletionStage` or
`Mono`. Unwrap the return type:

```java
// 1.x
@HttpRoute(method = HttpMethod.GET, path = "/items/{id}")
@Json
CompletionStage<ItemResponse> getItemAsync(@Path String id);

// 2.0
@HttpRoute(method = HttpMethod.GET, path = "/items/{id}")
@Json
ItemResponse getItem(@Path String id);
```

Then rewrite the call chain: `thenApply(f)` becomes a plain call on the result, `thenCompose(f)`
becomes a second statement, `exceptionally(f)` becomes a `catch`, and `.join()` / `.block()`
disappears.

> The unresolvable mapper is also a **diagnostic trap**: while it is in the graph, the processor
> may report an error against a different, perfectly correct component. Remove every async
> signature first, then read what is left.

---

## Kotlin suspend

A `suspend` client method is rejected outright by KSP, with a message that names the method:

```
HTTP client method is invalid:
  getItem

Problem:
  Suspend methods are not supported by the HTTP client generator.
...
Fix:
  Remove suspend from the method or expose an async return type supported by Kora HTTP client.
```

```kotlin
// 1.x
@HttpRoute(method = HttpMethod.GET, path = "/pets/{id}")
@Json
suspend fun get(@Path id: Long): Pet

// 2.0
@HttpRoute(method = HttpMethod.GET, path = "/pets/{id}")
@Json
fun get(@Path id: Long): Pet
```

**Do not keep a coroutine facade in the client interface.** This compiles, because the `default`
method is not a generated route:

```kotlin
// WRONG — still a public coroutine API, masking a synchronous contract
@HttpClient("httpClient.petApi")
interface PetApiClient {

    @HttpRoute(method = HttpMethod.GET, path = "/pets/{id}")
    @Json
    fun getBlocking(@Path id: Long): Pet

    suspend fun get(id: Long): Pet = withContext(Dispatchers.IO) { getBlocking(id) }
}
```

Two things are wrong with it. It keeps every caller on coroutines, so the migration never
finishes; and `Dispatchers.IO` is a platform-thread pool whose entire purpose is to keep blocking
work off other threads — on virtual threads that is pure overhead plus an artificial concurrency
cap.

If business logic genuinely still needs coroutines, put the bridge in an application service, never
in the `@HttpClient` interface. A duplicate suspend-only client is better deleted outright.

---

## Migration table

| 1.x construct | 2.0 replacement |
|---|---|
| `stage.thenApply(f)` | `f(client.call())` |
| `stage.thenCompose(f)` | two sequential statements |
| `stage.exceptionally(f)` | `try { … } catch (HttpClientException e) { … }` |
| `CompletableFuture.allOf(…).join()` | `StructuredTaskScope` (below) |
| `Mono.zip(a, b)` | `StructuredTaskScope` (below) |
| `Flux.fromIterable(ids).flatMap(client::get)` | a loop, or a scope forking one subtask per id |
| `coroutineScope { async { } … awaitAll() }` | `StructuredTaskScope` |
| `runBlocking { client.get(id) }` | `client.get(id)` |
| `withTimeout(d) { … }` | `requestTimeout` in the client config, or `@Timeout(Spec.class)` |
| `Flow` / channels | no mechanical analogue — redesign |

---

## Parallel calls

**Start sequential.** Two blocking calls on a virtual thread cost the sum of their latencies with
no thread-pool pressure; that is acceptable far more often than in a platform-thread world. Reach
for concurrency only when the latencies actually matter.

When they do, the framework's own answer — quoted verbatim in the KSP error above — is Java
**Structured Concurrency** (`java.util.concurrent.StructuredTaskScope`):

```java
Dashboard getDashboard(long userId) throws InterruptedException {
    try (var scope = StructuredTaskScope.open(
            StructuredTaskScope.Joiner.<Object>awaitAllSuccessfulOrThrow())) {

        var profile = scope.fork(() -> profileHttpClient.getProfile(userId));
        var recommendations = scope.fork(() -> recommendationsHttpClient.getForUser(userId));

        scope.join();
        return new Dashboard(profile.get(), recommendations.get());
    }
}
```

```kotlin
fun getDashboard(userId: Long): Dashboard =
    StructuredTaskScope.open(
        StructuredTaskScope.Joiner.awaitAllSuccessfulOrThrow<Any>(),
    ).use { scope ->
        val profile = scope.fork(Callable { profileHttpClient.getProfile(userId) })
        val recommendations = scope.fork(Callable { recommendationsHttpClient.getForUser(userId) })

        scope.join()
        Dashboard(profile.get(), recommendations.get())
    }
```

> ⚠ **`StructuredTaskScope` is a preview API and its shape has changed in every JDK since 21** —
> the factory method, the `Joiner` type and the result of `join()` are all different across
> iterations. The snippet above matches the iteration the Kora 2.0 migration guides were written
> against. **Open the Javadoc of the JDK you are actually compiling with and re-derive the call**
> instead of copying this. Do not pin an older preview, and do not adopt an EA JDK in production
> just to get a newer one.

Enabling preview is not one flag — it must be on for every compile and every JVM launch:

```kotlin
val previewJava = 26 // the latest GA feature release at the time of the migration

java { toolchain.languageVersion.set(JavaLanguageVersion.of(previewJava)) }

kotlin {
    jvmToolchain(previewJava)
    compilerOptions {
        freeCompilerArgs.addAll("-Xjdk-release=$previewJava", "-Xjvm-enable-preview")
    }
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(previewJava)
    options.compilerArgs.add("--enable-preview")
}
tasks.withType<Test>().configureEach { jvmArgs("--enable-preview") }
tasks.withType<JavaExec>().configureEach { jvmArgs("--enable-preview") }

application {
    applicationDefaultJvmArgs = listOf("--enable-preview", "-Dfile.encoding=UTF-8")
}
```

The build image and the runtime image must be the same JDK major version — preview class files are
rejected by any other release.

Policy is a semantic decision, not a mechanical one: fail-fast with sibling cancellation is
`awaitAllSuccessfulOrThrow()`; "first success wins" and supervisor-style "collect what succeeded"
use a different `Joiner` plus explicit `Subtask.state()` inspection; `withTimeout` becomes a
timeout in the scope configuration. Cancellation and exception propagation need their own tests.

**If you do not want preview APIs**, an `ExecutorService` over virtual threads
(`Executors.newVirtualThreadPerTaskExecutor()`) with `invokeAll` covers simple fan-out without any
flags — at the cost of manual cancellation handling.

---

## Timeouts instead of composition

What used to be `withTimeout`, `Mono.timeout` or an orchestration-level deadline is configuration:

```hocon
httpClient.itemApi {
  url = "http://items-service:8080"
  requestTimeout = 5s        # whole call: DNS, connect, write, server processing, read
  getItem { requestTimeout = 1s }
}
```

Retries, circuit breaking and fallbacks are AOP annotations on the client interface — see
[error-handling-guide](error-handling-guide.md#resilience) and
[`kora-aop-resilient`](../../kora-aop-resilient/SKILL.md). None of them require an async return type.

---

## Tests

Once the contracts are synchronous:

- Drop `runBlocking` / `runTest` wrappers and coroutine test dispatchers.
- `coEvery`/`coVerify` become `every`/`verify`; `whenever(...).thenReturn(CompletableFuture...)`
  becomes a plain return value.
- Assertions on `HttpClientResponseException` replace assertions on a failed future.
- Delete `kotlinx-coroutines-test` when nothing else uses it.

An interceptor test no longer needs a `Context` argument:

```java
var response = interceptor.processRequest(request -> stubResponse, request);
```

---

## See also

- [declarative-client-reference](declarative-client-reference.md)
- [transports-reference](transports-reference.md)
- [error-handling-guide](error-handling-guide.md)
- [interceptors-reference](interceptors-reference.md)
