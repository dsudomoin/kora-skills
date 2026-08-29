# Configuration Reference

Three config sections belong to the HTTP server, and 1.x used different names for most of them.

| Section | Bound by | Contents |
|---|---|---|
| `httpServer` | `UndertowPublicHttpServerModule` | the public application server |
| `httpServer.system` | `UndertowSystemHttpServerModule` | probes and metrics |
| `httpServer.undertow` | `UndertowSystemHttpServerModule` | transport tuning, shared by both servers |

## Contents

- [Port migration from 1.x — silent failure](#port-migration-from-1x--silent-failure)
- [httpServer](#httpserver)
- [httpServer.system](#httpserversystem)
- [httpServer.undertow](#httpserverundertow)
- [Telemetry](#telemetry)
- [Environment substitution](#environment-substitution)
- [Full key rename table](#full-key-rename-table)

---

## Port migration from 1.x — silent failure

```hocon
# Kora 1.x                          # Kora 2.0
httpServer {                        httpServer {
  publicApiHttpPort = 8080            port = 8080
  privateApiHttpPort = 8085           system.port = 8085
}                                   }
```

A stale 1.x key is simply an **unrecognised HOCON key**. It is ignored without a warning, so each
server falls back to its own default:

- `HttpServerConfig.port()` → **8080**
- `SystemHttpServerConfig.port()` **overrides it** → **8085**

So a service that deliberately ran on non-default ports comes up **green, on the wrong ports** —
the process is healthy, the logs are clean, and probes, scrapers and load balancers hit nothing.
This is the dangerous case, and it is easy to miss precisely because nothing fails.

`Address already in use` only appears in the narrower situation where stale keys collapse both
servers onto one port.

> Older migration notes claim `SystemHttpServerConfig` inherits `port() = 8080` so both servers
> collide. That describes a pre-release alpha; the override to `8085` landed before `2.0.0.RC1` and
> is present in both `2.0.0.RC1` and `master`. Do not repeat the "both bind 8080" story.

**Verify after migrating** rather than trusting a clean startup: `curl` the public port and
`GET /system/readiness` on the system port and check both answer.

---

## httpServer

Every key below exists on `HttpServerConfig`; defaults are the interface's default methods.

```hocon
httpServer {
  port = 8080                        # default 8080
  ignoreTrailingSlash = false        # /path and /path/ as one route
  socketReadTimeout = "0s"           # zero disables
  socketWriteTimeout = "0s"          # zero disables
  socketKeepAliveEnabled = false
  headerKeepAliveEnabled = false
  headerServerDateEnabled = true
  shutdownWait = "30s"               # graceful shutdown grace period
  maxRequestBodySize = "256MiB"

  telemetry { … }                    # see below
}
```

There is **no** `virtualThreadsEnabled` key. Kora 2.0 always dispatches requests onto virtual
threads (`KoraVirtualThreadDispatchHttpHandler`); the 1.x toggle no longer exists. Nor are there
`tls`, `cors` or `blockingThreads` keys — do not invent them.

---

## httpServer.system

`SystemHttpServerConfig` extends `HttpServerConfig`, so every key above is also valid here, plus:

```hocon
httpServer.system {
  port = 8085                            # overridden default, NOT inherited 8080
  metricsPath   = "/metrics"
  readinessPath = "/system/readiness"
  livenessPath  = "/system/liveness"
}
```

| Endpoint | Default path | Served by |
|---|---|---|
| Metrics | `/metrics` | `MetricsHandler`, from the `MetricsScraper` in the graph |
| Readiness | `/system/readiness` | `ReadinessHandler`, over all `ReadinessProbe`s |
| Liveness | `/system/liveness` | `LivenessHandler`, over all `LivenessProbe`s |

Plugging `UndertowPublicHttpServerModule` into `@KoraApp` starts both servers — it extends
`UndertowSystemHttpServerModule`. There is no way to get the public server without the system one
via that module, and no `UndertowHttpServerModule` any more.

Server-scoped interceptors (`@Tag(HttpServer.class)`) do **not** apply to this server. Its router
is built by the `@SystemApi`-tagged factory module and therefore collects `@Tag(SystemApi.class)`
interceptors instead — a different tag, so probes and metrics run unintercepted. Restrict this port
with a network policy rather than trying to authenticate it.

---

## httpServer.undertow

Transport tuning moved out of `httpServer` into its own section, bound once and shared by both
servers (`UndertowConfig`):

```hocon
httpServer.undertow {
  ioThreads = 8                     # default: max(availableProcessors, 2)
  threadKeepAliveTimeout = "60s"
}
```

`ioThreads` and `threadKeepAliveTimeout` under `httpServer` directly are ignored — that is the
1.x location.

---

## Telemetry

`logging.enabled` and `metrics.enabled` default to **`false`**. Component metrics such as
`http_server_*` simply do not appear until you switch them on.

**This is a change from 1.x, and it is silent.** In Kora 1.x `httpServer.telemetry.metrics.enabled`
defaulted to `true`, so a config that never mentioned metrics still produced them. In 2.0 the same
config produces none — dashboards and alerts go blank without a single error. Enable it explicitly:

```hocon
httpServer.telemetry {
  logging {
    enabled = true                  # default FALSE
    stacktrace = true
    mask = "***"
    maskQueries = [ ]
    maskHeaders = [ "authorization", "cookie", "set-cookie" ]
    maxRequestBodyLogSize  = "2MiB"
    maxResponseBodyLogSize = "2MiB"
    # pathFull — nullable Boolean, log the full path instead of the route template
  }
  metrics {
    enabled = true                  # default FALSE
    slo = [ "1ms", "10ms", "50ms", "100ms", "200ms", "500ms",
            "1s", "2s", "5s", "10s", "20s", "30s", "60s", "90s" ]
    tags { app = "my-service" }     # extra tags on every metric
  }
  tracing {
    enabled = true                  # default true on httpServer
    tracePathFull = true
    attributes { env = "prod" }     # extra attributes on every span
  }
}
```

`slo` is a `Duration[]` in 2.0 (it was a `double[]` in 1.x). `DurationConfigValueMapper` accepts
both a duration string (`"100ms"`) and a bare number, which it reads as **milliseconds**, so the
1.x millisecond form keeps working. What does not survive is a 1.x config written against
`OpentelemetrySpec.V123`, where the values were **seconds** (`0.001`, `0.010`, …): each truncates to
`Duration.ofMillis(0)` and the histogram silently collapses. `OpentelemetrySpec` is gone in 2.0.

**Tracing is the exception on the system server.** `SystemHttpServerTracingConfig` overrides
`enabled()` to `false`, so the "tracing defaults to true" rule does not hold under
`httpServer.system` — probe and metrics traffic is not traced unless you ask for it.

Any example claiming to demonstrate request logging or HTTP metrics must enable them explicitly.

---

## Environment substitution

HOCON substitution is unchanged from 1.x:

```hocon
httpServer {
  port = 8080
  port = ${?HTTP_PORT}                     # override when the variable is set
  system.port = ${?HTTP_SYSTEM_PORT}
  maxRequestBodySize = ${?MAX_BODY_SIZE}
}
```

- `${VAR}` — required; startup fails if absent
- `${?VAR}` — optional; the assignment is skipped when absent, so write the default on the line
  above and let the substitution override it

---

## Full key rename table

| Kora 1.x | Kora 2.0 |
|---|---|
| `httpServer.publicApiHttpPort` | `httpServer.port` |
| `httpServer.privateApiHttpPort` | `httpServer.system.port` |
| `httpServer.privateApiHttpMetricsPath` | `httpServer.system.metricsPath` |
| `httpServer.privateApiHttpReadinessPath` | `httpServer.system.readinessPath` |
| `httpServer.privateApiHttpLivenessPath` | `httpServer.system.livenessPath` |
| `httpServer.ioThreads` | `httpServer.undertow.ioThreads` |
| `httpServer.threadKeepAliveTimeout` | `httpServer.undertow.threadKeepAliveTimeout` |
| `httpServer.blockingThreads` | removed — virtual threads are unconditional |
| `httpServer.virtualThreadsEnabled` | removed — always on |
| `httpServer.telemetry.logging.pathTemplate` | `httpServer.telemetry.logging.pathFull` — same knob, **opposite polarity** (`pathTemplate = true` ⇔ `pathFull = false`); both are optional/nullable |
| `httpServer.telemetry.metrics.enabled` default `true` | default **`false`** — must be set explicitly |

The same keys apply to YAML (`config-yaml`) with YAML syntax.

Embedded HOCON in tests counts too: `KoraConfigModification.ofString("""…""")` blocks carry the
same keys and are missed by any scan that only looks at `.conf` and `.yaml` files.

**See also:** [Controller & Routing](controller-routing-reference.md), [Interceptors](interceptors-reference.md).
