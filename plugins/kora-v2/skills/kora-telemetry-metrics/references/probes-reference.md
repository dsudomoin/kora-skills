# Liveness and Readiness Probes (Kora 2.0)

Probes share the system HTTP server with `/metrics`, which is why they live in this skill.
Everything below is read from `core/common/.../{liveness,readiness}` and
`http/http-server-common/.../system/`.

## Contents

- [There is no `probes` module](#no-probes-module)
- [Endpoints and configuration](#endpoints)
- [The 2.0 probe API](#probe-api)
- [What the handler actually returns](#handler-behaviour)
- [Probes Kora registers for you](#builtin-probes)
- [Custom probes](#custom-probes)
- [Kubernetes](#kubernetes)
- [Changed from 1.x](#changed-from-1x)
- [Debugging](#debugging)

---

## There is no `probes` module { #no-probes-module }

No `io.koraframework:probes` artifact exists. Probes are built into the HTTP server:
`SystemHttpServerModule` (in `io.koraframework:http-server-common`) registers
`LivenessHandler`, `ReadinessHandler` and `MetricsHandler`, and
`UndertowPublicHttpServerModule extends UndertowSystemHttpServerModule`, so adding
`io.koraframework:http-server-undertow` and the public module is enough.

The probe **interfaces** live in `io.koraframework:common`, not in the HTTP module — a component
can implement `ReadinessProbe` without depending on the HTTP server.

---

## Endpoints and configuration { #endpoints }

`SystemHttpServerConfig`, config root `httpServer.system`:

```java
@Override default int port()  { return 8085; }
default String metricsPath()   { return "/metrics"; }
default String readinessPath() { return "/system/readiness"; }
default String livenessPath()  { return "/system/liveness"; }
```

```hocon
httpServer {
  port = 8080                            # public business traffic
  system {
    port = 8085                          # metrics + probes
    livenessPath = "/system/liveness"
    readinessPath = "/system/readiness"
    metricsPath = "/metrics"
  }
}
```

All three handlers are `GET`-only (`ProbeHandler.method()` returns `"GET"`).

---

## The 2.0 probe API { #probe-api }

Two interfaces, one method each, **returning `null` on success**:

```java
package io.koraframework.common.readiness;

public interface ReadinessProbe {
    @Nullable ReadinessProbeFailure probe() throws Exception;
}

public record ReadinessProbeFailure(String message) {}
```

```java
package io.koraframework.common.liveness;

public interface LivenessProbe {
    @Nullable LivenessProbeFailure probe() throws Exception;
}

public record LivenessProbeFailure(String message) {}
```

There is no `Result` type, no `Result.success()` / `Result.failure(...)` and no `check()` method —
those were the 1.x shape. `probe()` may throw; the handler catches it.

---

## What the handler actually returns { #handler-behaviour }

`ProbeHandler.handleProbes` runs every registered probe **concurrently on virtual threads**
(`Executors.newVirtualThreadPerTaskExecutor()`) and aggregates:

| Situation | Status | Body |
|---|---|---|
| No probes registered at all | `200` | `OK` |
| A probe component is not initialised yet (`PromiseOf` empty) | `503` | `Probe is not ready yet` |
| Every probe returned `null` | `200` | `OK` |
| Any probe returned a failure | `503` | that probe's `message()` |
| Any probe threw | `503` | `Probe failed: <exception message>` |
| Aggregation did not finish in **30 s** | `408` | `Probe failed: timeout` |
| Aggregation failed outright | `500` | the execution exception's message |

Two consequences worth planning around:

- **The failure message is returned in the HTTP body.** Do not put credentials, internal hostnames
  or raw exception detail into a `ProbeFailure` message unless the system port is genuinely private.
- **The 30-second cap is fixed**, and probes run in parallel, so one slow probe delays the whole
  response up to that limit. Give any probe that performs I/O its own short timeout.

The readiness aggregation is strict AND: one failing probe fails the endpoint.

---

## Probes Kora registers for you { #builtin-probes }

Some framework components implement `ReadinessProbe` themselves, so `/system/readiness` is rarely
"empty" in a real service:

| Component | Behaviour |
|---|---|
| `UndertowHttpServer` (`http-server-undertow`) | fails while the server state is `INIT` (`"HTTP Server <name> (Undertow) init"`) or `SHUTDOWN`, ready in `RUN`. One instance per server, so both `kora-undertow` and `kora-undertow-system` contribute |
| `GrpcServer` (`grpc-server`) | same shape for the gRPC server lifecycle |
| `JdbcDataSource` (`database-jdbc`) | **opt-in** — `probe()` only touches the database when `jdbc.readinessProbe = true`, and that key defaults to **`false`**. When on, it opens a connection and calls `isValid(jdbc.validationTimeout)` (default `5s`) |

So a database readiness check does **not** need a hand-written probe:

```hocon
jdbc {
  readinessProbe = true
  validationTimeout = "2s"
}
```

Write a custom probe only for something Kora does not already cover.

Kora ships **no** built-in `LivenessProbe` implementation — `/system/liveness` answers `200 OK`
until you add one.

---

## Custom probes { #custom-probes }

Register a probe by declaring it as a `@Component` implementing the interface. It is collected via
`All<PromiseOf<ReadinessProbe>>`, so no extra annotation or registration is needed.

### Readiness — Java

```java
package com.example;

import io.koraframework.common.annotation.Component;
import io.koraframework.common.readiness.ReadinessProbe;
import io.koraframework.common.readiness.ReadinessProbeFailure;
import org.jspecify.annotations.Nullable;

import java.util.concurrent.atomic.AtomicBoolean;

@Component
public final class ReferenceDataReadinessProbe implements ReadinessProbe {

    private final AtomicBoolean loaded = new AtomicBoolean(false);

    public void markLoaded() {
        this.loaded.set(true);
    }

    @Override
    public @Nullable ReadinessProbeFailure probe() {
        return this.loaded.get()
                ? null
                : new ReadinessProbeFailure("Reference data cache is still warming up");
    }
}
```

Returning `null` means ready. Letting a checked exception propagate is also fine — `probe()` is
declared `throws Exception` and the handler turns it into `503` with `Probe failed: …`.

### Readiness — Kotlin

```kotlin
package com.example

import io.koraframework.common.annotation.Component
import io.koraframework.common.readiness.ReadinessProbe
import io.koraframework.common.readiness.ReadinessProbeFailure
import java.util.concurrent.atomic.AtomicBoolean

@Component
class ReferenceDataReadinessProbe : ReadinessProbe {

    private val loaded = AtomicBoolean(false)

    fun markLoaded() {
        loaded.set(true)
    }

    override fun probe(): ReadinessProbeFailure? =
        if (loaded.get()) null
        else ReadinessProbeFailure("Reference data cache is still warming up")
}
```

The Kotlin return type must be nullable (`ReadinessProbeFailure?`). Kora contracts are
`@NullMarked`, so declaring a non-null return type makes the compiler report
`'probe' overrides nothing` — a message that says nothing about nullability.

### Liveness — Java

```java
package com.example;

import io.koraframework.common.annotation.Component;
import io.koraframework.common.liveness.LivenessProbe;
import io.koraframework.common.liveness.LivenessProbeFailure;
import org.jspecify.annotations.Nullable;

@Component
public final class ApplicationHealthProbe implements LivenessProbe {

    @Override
    public @Nullable LivenessProbeFailure probe() {
        return isHealthy() ? null : new LivenessProbeFailure("Unhealthy internal state");
    }

    private boolean isHealthy() {
        return true;
    }
}
```

Liveness should test only whether the process must be restarted. Checking an external dependency
from a liveness probe turns a downstream outage into a restart storm — that belongs in readiness.

### Choosing between them

| | Liveness | Readiness |
|---|---|---|
| Failing means | restart the pod | take the pod out of the load balancer |
| Should check | in-process invariants, deadlocked state | database, cache, broker, warm-up completion |
| Should **not** check | external dependencies | anything requiring a restart to fix |
| Contracts are synchronous | yes — `probe()` returns a value, virtual threads handle concurrency | same |

---

## Kubernetes { #kubernetes }

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: order-service
spec:
  containers:
    - name: app
      image: order-service:latest
      ports:
        - name: http
          containerPort: 8080
        - name: system
          containerPort: 8085
      livenessProbe:
        httpGet:
          path: /system/liveness
          port: system
        initialDelaySeconds: 30
        periodSeconds: 10
        timeoutSeconds: 5
        failureThreshold: 3
      readinessProbe:
        httpGet:
          path: /system/readiness
          port: system
        initialDelaySeconds: 5
        periodSeconds: 5
        timeoutSeconds: 3
        failureThreshold: 1
```

Point both at the **system** port. Keep `timeoutSeconds` well under the handler's 30-second cap so
Kubernetes gives up before the aggregation does.

---

## Changed from 1.x { #changed-from-1x }

| Kora 1.x | Kora 2.0 |
|---|---|
| `ru.tinkoff.kora.http.server.common.ReadinessProbe` | **`io.koraframework.common.readiness.ReadinessProbe`** |
| `ru.tinkoff.kora.http.server.common.LivenessProbe` | **`io.koraframework.common.liveness.LivenessProbe`** |
| `Result check()` | **`@Nullable ReadinessProbeFailure probe() throws Exception`** |
| `Result.success()` | **`return null`** |
| `Result.failure("reason")` | **`new ReadinessProbeFailure("reason")`** |
| `httpServer.privateApiHttpPort` | **`httpServer.system.port`** |
| `httpServer.privateApiHttpLivenessPath` | **`httpServer.system.livenessPath`** |
| `httpServer.privateApiHttpReadinessPath` | **`httpServer.system.readinessPath`** |
| "failure reasons are not exposed in the response" | **false in 2.0** — the message is the 503 body |
| `UndertowHttpServerModule` | **`UndertowPublicHttpServerModule`** |

The 1.x defaults `/system/liveness` and `/system/readiness` are unchanged in 2.0, so a stale
`privateApiHttpLivenessPath` key usually breaks nothing visible — right up until someone had
customised the path, at which point the endpoint silently moves back to the default.

---

## Debugging { #debugging }

```bash
curl -i http://localhost:8085/system/liveness
curl -i http://localhost:8085/system/readiness
```

| Symptom | Cause |
|---|---|
| `503 Probe is not ready yet` | A probe component has not finished graph initialisation |
| `503 <your message>` | A probe returned a failure — the body names which check failed |
| `503 Probe failed: …` | A probe threw |
| `408 Probe failed: timeout` | Aggregate probe time exceeded 30 s |
| `200 OK` on `/system/liveness` always | Correct — Kora ships no built-in `LivenessProbe` |
| `200 OK` on `/system/readiness` while the database is down | `jdbc.readinessProbe` is still at its `false` default |
| `404` | Wrong port (the public server) or a customised `*Path` you forgot |

A custom probe that is never called is almost always a missing `@Component` — Kora prunes graph
nodes nothing references, and `All<PromiseOf<ReadinessProbe>>` only collects registered components.

---

## See also

- [metrics-export-reference.md](metrics-export-reference.md) — `/metrics` on the same system server
- [metrics-config-reference.md](metrics-config-reference.md#system-server) — the `httpServer.system` config block
- [kora-http-server configuration](../../kora-http-server/references/configuration-reference.md)
- [kora-di-runtime](../../kora-di-runtime/SKILL.md) — `All<T>`, `PromiseOf<T>`, `@Root` and graph pruning
