# Assertion Patterns Reference

Assertion and verification patterns for Kora 2.0 Java tests. Kora ships no assertion API of its
own — these are plain JUnit 5, AssertJ, Mockito and Awaitility, plus the few Kora types a test
asserts against.

## Contents

- [Synchronous contracts change what you assert](#synchronous-contracts-change-what-you-assert)
- [JUnit 5 assertions](#junit-5-assertions)
- [Kora exceptions](#kora-exceptions)
- [AssertJ](#assertj)
- [Mockito verify](#mockito-verify)
- [Asynchronous side effects](#asynchronous-side-effects)
- [Metrics assertions](#metrics-assertions)
- [JSON assertions](#json-assertions)
- [Database assertions](#database-assertions)
- [Best practices](#best-practices)

---

## Synchronous contracts change what you assert

Kora 2.0 contracts are synchronous, executed on virtual threads. Reactor `Mono`/`Flux`,
`CompletionStage` and Kotlin `suspend` are no longer Kora contracts for repositories, controllers
or HTTP clients, and `Context` no longer exists anywhere in the framework.

A test ported from Kora 1.x therefore loses its whole reactive assertion layer:

| Kora 1.x test shape | Kora 2.0 |
|---|---|
| `StepVerifier.create(service.getUser("1")).expectNextMatches(…).verifyComplete()` | `var user = service.getUser("1"); assertEquals(…, user)` |
| `.block()` / `.join()` / `.get()` on the result | the method already returns the value |
| `StepVerifier … .expectError(X.class).verify()` | `assertThrows(X.class, () -> service.getUser("missing"))` |
| `Mono.just(x)` in a Mockito stub | `thenReturn(x)` |
| `io.projectreactor:reactor-test` dependency | delete it |

Do not "keep the scenario" by asserting on a `CompletableFuture` you wrapped yourself — that tests
your wrapper, not the service. Assert the returned value, and use Awaitility only where the
production code really is asynchronous (a Kafka listener, a scheduled job).

---

## JUnit 5 assertions

```java
import static org.junit.jupiter.api.Assertions.*;

@Test
void shouldCreateUser() {
    var result = userService.createUser(new UserRequest("John", "john@example.com"));

    assertNotNull(result);
    assertEquals("John", result.name());
    assertEquals("john@example.com", result.email());
}
```

Group independent checks so all failures report at once:

```java
assertAll("user response",
    () -> assertNotNull(result),
    () -> assertEquals("John", result.name()),
    () -> assertNotNull(result.id()));
```

---

## Kora exceptions

`HttpServerResponseException` from `io.koraframework.http.server.common.response` carries the
status code on `code()`:

```java
var exception = assertThrows(HttpServerResponseException.class,
        () -> userService.deleteUser("missing"));

assertEquals(404, exception.code());
```

Other types a component test commonly asserts on:

| Exception | Package | Raised by |
|---|---|---|
| `HttpServerResponseException` | `io.koraframework.http.server.common.response` | Server-side error mapping |
| `HttpClientResponseException` | `io.koraframework.http.client.common.exception` | A declarative `@HttpClient` on a non-2xx response |
| `ViolationException` | `io.koraframework.validation.common` | `@Valid` / `@Validate` |
| `CallNotPermittedException` | `io.koraframework.resilient.circuitbreaker.exception` | An open circuit breaker |
| `ConfigValueException` | `io.koraframework.config.common.exception` | A missing config value during graph init |

---

## AssertJ

```groovy
testImplementation "org.assertj:assertj-core:3.27.7"
```

```java
import static org.assertj.core.api.Assertions.*;

assertThat(result)
    .isNotNull()
    .extracting(UserResponse::email)
    .isEqualTo("john@example.com");

assertThat(users)
    .hasSize(3)
    .extracting(UserResponse::email)
    .containsExactlyInAnyOrder("a@test.com", "b@test.com", "c@test.com");

assertThatThrownBy(() -> userService.createUser(new UserRequest("", "")))
    .isInstanceOf(ViolationException.class)
    .hasMessageContaining("name");
```

---

## Mockito verify

```java
import static org.mockito.Mockito.*;

verify(userRepository).save("John", "john@example.com");
verify(userRepository, times(3)).findById(any());
verify(userRepository, never()).deleteById(any());

var inOrder = inOrder(userRepository);
inOrder.verify(userRepository).save(any());
inOrder.verify(userRepository).update(any(), any(), any());
```

`ArgumentCaptor`:

```java
var captor = ArgumentCaptor.forClass(User.class);
verify(userRepository).save(captor.capture());
assertThat(captor.getValue().email()).isEqualTo("john@example.com");
```

Verification only sees mocks that entered the graph — that is, elements carrying both `@Mock` (or
`@Spy`) and `@TestComponent`.

---

## Asynchronous side effects

Where production code really is asynchronous — a Kafka listener, a `@Schedule*` job, a background
`Lifecycle` — poll for the observable effect:

```groovy
testImplementation "org.awaitility:awaitility:4.3.0"
```

```java
Awaitility.await()
    .atMost(Duration.ofSeconds(15))
    .pollExecutorService(Executors.newSingleThreadExecutor())
    .until(() -> userService.getUser(userId).isPresent());

Awaitility.await()
    .atMost(Duration.ofSeconds(10))
    .untilAsserted(() -> assertEquals(1, consumer.received().size()));
```

Never `Thread.sleep` — it is either flaky or slow, usually both.

---

## Metrics assertions

Inject the Micrometer `MeterRegistry` like any other component and read the meters back:

```java
@KoraAppTest(Application.class)
class ObservabilityAppTest {

    @TestComponent
    private UserService userService;
    @TestComponent
    private MeterRegistry meterRegistry;

    @Test
    void userCreationUpdatesCustomMetrics() {
        userService.createUser(new UserRequest("Alice", "alice@example.com"));

        var counter = meterRegistry.find("user.creation.total").counter();
        var timer = meterRegistry.find("user.creation.duration").timer();

        assertNotNull(counter);
        assertEquals(1.0d, counter.count());
        assertEquals(1L, timer.count());
    }
}
```

Metrics your own code registers on the `MeterRegistry` always work. **Kora's component metrics
(`http_server_*`, `http_client_*`, `db_*`, Kafka consumer/producer meters) do not** —
`TelemetryConfig.MetricsConfig.enabled()` defaults to `false`, so the meters are never registered.
A test asserting on them must switch telemetry on for that component in the test config:

```hocon
httpServer {
  telemetry.metrics.enabled = true
  telemetry.logging.enabled = true
}
jdbc.telemetry.metrics.enabled = true
```

Logging follows the same default (`false`); tracing defaults to `true`, except under
`httpServer.system`, where it is overridden to `false`.

---

## JSON assertions

Compare serialized output with a JSON library rather than string equality:

```groovy
testImplementation "org.json:json:20231013"
testImplementation "org.skyscreamer:jsonassert:1.5.1"
```

```java
JSONAssert.assertEquals("""
        {"id":"1","name":"John","email":"john@example.com"}
        """, actualJson, JSONCompareMode.LENIENT);
```

Kora's own JSON lives in `io.koraframework.json.common` (`JsonReader`/`JsonWriter`, artifact
`json-common`). The `*Unchecked` methods are gone and the plain ones no longer declare checked
exceptions, so `toByteArray`/`toString`/`read` need no `try/catch (IOException)` — writing one is
a compile error. `JsonReader<T>.read(data)` returns a nullable value.

---

## Database assertions

Assert persistence through a test-only repository declared in the `TestApplication` graph rather
than through raw SQL — it exercises the same Kora JDBC stack the application uses.

```java
@TestComponent
private TestApplication.TestUserRepository testUserRepository;

@Test
void shouldPersistUser() {
    userService.createUser(new UserRequest("John", "john@example.com"));

    var rows = testUserRepository.findAll();

    assertThat(rows).hasSize(1);
    assertThat(rows.get(0).email()).isEqualTo("john@example.com");
}
```

See [testcontainers-jdbc-reference.md](testcontainers-jdbc-reference.md) for the submodule pattern
and its build wiring.

---

## Best practices

1. Assert the value the synchronous method returned; there is nothing to unwrap in Kora 2.0.
2. Group independent checks with `assertAll` for better diagnostics.
3. Reach for AssertJ when assertions get expressive (collections, nested fields).
4. Use `ArgumentCaptor` when matchers cannot express the assertion.
5. Enable telemetry explicitly before asserting on Kora component metrics.
6. Use Awaitility only for genuinely asynchronous production behaviour, never as a sleep.
7. Capture generated values from the result instead of hardcoding ids.
