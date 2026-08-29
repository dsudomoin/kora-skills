# Kora 2.0 Source & Example Map

Lookup table for level 5 of the R1 routing chain. Read this **only after** the relevant sub-skill
and its `references/` did not answer the question.

## There is no Kora 2.0 documentation site

The `kora-docs` repository documents Kora **1.x** on every branch that exists today. Its
`feature/kora-2.0` branch reorganises the site into `docs/v1/` and `docs/v2/`, but `docs/v2` is
currently a byte-identical copy of the 1.x pages — it still describes `ru.tinkoff.kora`, and still
ships pages for `database-r2dbc` and `database-vertx`, integrations that no longer exist in 2.0.

**Consequences:**

- Never resolve a Kora 2.0 API question from `kora-docs`. It will give you 1.x answers that look
  authoritative.
- `kora-docs` is usable only as **1.x conceptual background** — the shape of a feature, the
  vocabulary, the intent — and only when you say out loud that that is what you are doing.
- Level 5 for Kora 2.0 is the **framework source** plus the **migrated example applications**,
  both fetched by the R0 block in [`../SKILL.md`](../SKILL.md).

## Path templates

| Source | Path template | Ref |
|---|---|---|
| Framework source | `.kora-agent/kora-source-2.0/<area>/<module>/src/main/java/...` | tag `2.0.0.RC1` |
| Framework tests (behaviour, generated-code shape) | `.kora-agent/kora-source-2.0/<area>/<module>/src/test/...` | tag `2.0.0.RC1` |
| Version catalog (third-party versions) | `.kora-agent/kora-source-2.0/gradle/libs.versions.toml` | tag `2.0.0.RC1` |
| Artifact list | `.kora-agent/kora-source-2.0/settings.gradle` | tag `2.0.0.RC1` |
| Example apps | `.kora-agent/kora-examples-2.0/examples/java/<app>/` | branch `migration/2.0` |
| Guide apps | `.kora-agent/kora-examples-2.0/guides/java/<app>/` | branch `migration/2.0` |
| Migration corpus (1.x → 2.0) | `.kora-agent/kora-examples-2.0/migration/` | branch `migration/2.0` |

**Kotlin variants:** every `kora-java-*` app has a Kotlin twin — replace `java` with `kotlin`
(`kora-java-http-server` → `kora-kotlin-http-server`). The reference module the Java migration guide
was written against is `examples/java/kora-java-crud`; the Kotlin counterpart is
`examples/kotlin/kora-kotlin-crud`.

**Precedence inside level 5:** framework source outranks the examples. An example can lag behind a
fix; the source cannot. Framework **tests** are the best evidence for generated-code shape and for
defaults that no prose states.

## Module map

| Domain | Framework source | Example apps | Guide apps |
|---|---|---|---|
| Bootstrap / DI | `core/common`, `core/application-graph`, `core/kora-app-annotation-processor`, `core/kora-app-symbol-processor` | `kora-java-helloworld`, `kora-java-crud`, `kora-java-crud-submodule` | `kora-java-guide-getting-started-app`, `kora-java-guide-dependency-injection-introduction-app`, `kora-java-guide-dependency-injection/*` |
| Config | `config/config-common`, `config/config-hocon`, `config/config-yaml`, `config/config-annotation-processor`, `config/config-symbol-processor` | `kora-java-config-hocon`, `kora-java-config-yaml` | `kora-java-guide-config-hocon-app`, `kora-java-guide-config-yaml-app` |
| HTTP Server | `http/http-common`, `http/http-server-common`, `http/http-server-undertow`, `http/http-server-annotation-processor`, `http/http-server-symbol-processor` | `kora-java-http-server`, `kora-java-crud` | `kora-java-guide-http-server-app`, `kora-java-guide-http-server-advanced-app` |
| HTTP Client | `http/http-client-common`, `http/http-client-ok`, `http/http-client-jdk`, `http/http-client-apache`, `http/http-client-annotation-processor`, `http/http-client-symbol-processor` | `kora-java-http-client` | `kora-java-guide-http-client-app`, `kora-java-guide-http-client-advanced-app` |
| OpenAPI | `openapi/openapi-generator`, `openapi/openapi-management` | `kora-java-openapi-generator-http-server`, `kora-java-openapi-generator-http-client`, `kora-java-crud` | `kora-java-guide-openapi-http-server-app`, `kora-java-guide-openapi-http-server-advanced-app`, `kora-java-guide-openapi-http-client-app` |
| Database JDBC | `database/database-common`, `database/database-jdbc`, `database/database-annotation-processor`, `database/database-symbol-processor` | `kora-java-database-jdbc`, `kora-java-crud`, `kora-java-petclinic` | `kora-java-guide-database-jdbc-app`, `kora-java-guide-database-jdbc-advanced-app` |
| Database Cassandra | `database/database-cassandra` | `kora-java-database-cassandra` | `kora-java-guide-database-cassandra-app` |
| Migrations | `database/database-flyway`, `database/database-liquibase` | `kora-java-crud` (Flyway) | `kora-java-guide-database-jdbc-advanced-app` |
| gRPC | `grpc/grpc-server`, `grpc/grpc-client`, `grpc/grpc-client-annotation-processor`, `grpc/grpc-client-symbol-processor` | `kora-java-grpc-server`, `kora-java-grpc-client` | `kora-java-guide-grpc-server-app`, `kora-java-guide-grpc-server-advanced-app`, `kora-java-guide-grpc-client-app`, `kora-java-guide-grpc-client-advanced-app` |
| Kafka | `kafka/kafka`, `kafka/kafka-annotation-processor`, `kafka/kafka-symbol-processor` | `kora-java-kafka` | `kora-java-guide-messaging-kafka-app` |
| JSON | `json/json-common`, `json/jackson-module`, `json/json-annotation-processor`, `json/json-symbol-processor` | `kora-java-json` | `kora-java-guide-json-app` |
| Validation | `validation/validation-common`, `validation/validation-module`, `validation/validation-annotation-processor`, `validation/validation-symbol-processor` | `kora-java-validation` | `kora-java-guide-validation-app` |
| Telemetry — metrics | `telemetry/telemetry-common`, `telemetry/micrometer-common`, `telemetry/micrometer-module` | `kora-java-telemetry` | `kora-java-guide-observability-app` |
| Telemetry — tracing | `telemetry/opentelemetry-common`, `telemetry/opentelemetry-tracing`, `telemetry/opentelemetry-tracing-exporter-grpc`, `telemetry/opentelemetry-tracing-exporter-http` | `kora-java-telemetry` | `kora-java-guide-observability-app` |
| Logging | `logging/logging-common`, `logging/logging-logback`, `logging/logging-annotation-processor`, `logging/logging-symbol-processor` | `kora-java-telemetry` | `kora-java-guide-observability-app` |
| Cache | `cache/cache-common`, `cache/cache-caffeine`, `cache/cache-redis-common`, `cache/cache-redis-lettuce`, `cache/cache-annotation-processor`, `cache/cache-symbol-processor` | `kora-java-cache-caffeine`, `kora-java-cache-redis` | `kora-java-guide-cache-app`, `kora-java-guide-cache-multi-level-app` |
| Resilience | `resilient/resilient-kora`, `resilient/resilient-annotation-processor`, `resilient/resilient-symbol-processor` | `kora-java-resilient`, `kora-java-crud` | `kora-java-guide-resilient-app` |
| Scheduling | `scheduling/scheduling-common`, `scheduling/scheduling-jdk`, `scheduling/scheduling-quartz`, `scheduling/scheduling-annotation-processor`, `scheduling/scheduling-symbol-processor` | `kora-java-scheduling-jdk`, `kora-java-scheduling-quartz` | — |
| S3 | `s3/s3-client-aws`, `experimental/s3-client-kora`, `experimental/s3-client-annotation-processor`, `experimental/s3-client-symbol-processor` | `kora-java-s3-client-aws`, `kora-java-s3-client-kora` | `kora-java-guide-s3-app` |
| Mapping | `mapping/mapstruct-java-extension`, `mapping/mapstruct-ksp-extension`, `mapping/konvert-ksp-extension` | `kora-java-crud` (MapStruct) | — |
| SOAP | `http/soap-client`, `http/soap-client-annotation-processor`, `http/soap-client-symbol-processor` | `kora-java-soap-client` | — |
| Testing | `test/test-junit5` | every example's `src/test` | `kora-java-guide-testing-junit-app`, `kora-java-guide-testing-integration-app`, `kora-java-guide-testing-black-box-app` |

All three telemetry domains share one guide app — `kora-java-guide-observability-app`. The
documentation guides `observability-metrics.md`, `observability-tracing.md` and
`observability-probes.md` exist only in the 1.x doc set and have no separate 2.0 app.

## Removed in Kora 2.0 — do not look for them, do not recommend them

These are **gone**, not merely undocumented or discouraged. Any request that assumes them needs a
redesign conversation, not a workaround.

| 1.x feature | Status in 2.0 | Replacement |
|---|---|---|
| `database-r2dbc` | removed | synchronous `database-jdbc` on virtual threads |
| `database-vertx` | removed | synchronous `database-jdbc` on virtual threads |
| Reactive / `suspend` repository, controller and HTTP-client contracts | removed | synchronous contracts |
| `Context` | removed from the whole framework | rewrite the signature |
| `http-client-async` | removed | `http-client-jdk`, `http-client-ok`, `http-client-apache` |
| `s3-client-minio` | removed | `experimental:s3-client-kora`; MinIO still works as an S3-compatible **server** for tests |
| `json-module` | renamed | `json-common` |
| `cache-redis` | replaced | `cache-redis-lettuce` (plus transport-neutral `cache-redis-common`) |
| `kora-parent` | replaced | `kora-bom` |
| RapiDoc viewer | replaced | Scalar (`openapi.management.scalar`) |
| `java-reactive-*`, `kotlin-suspend-*`, `kotlin-reactive-*` OpenAPI modes | removed | `java-client`, `java-server`, `kotlin-client`, `kotlin-server` |

`kora-parent` and `cache-redis` still appear in the `io/koraframework/` directory listing on Maven
Central. Those are 1.x/alpha leftovers; neither is constrained by the `2.0.0.RC1` BOM. Directory
presence is not availability.

## Out of scope for this plugin

No sub-skill covers these. Read the framework source and the example apps directly, and say to the
user that you are working outside the plugin's vetted material.

| Area | Framework source | Example apps |
|---|---|---|
| Camunda 7 (BPMN / REST) | `experimental/camunda-engine-bpmn`, `experimental/camunda-rest-undertow` | `kora-java-camunda-engine` |
| Camunda 8 / Zeebe worker | `experimental/camunda-zeebe-worker`, `experimental/camunda-zeebe-worker-annotation-processor`, `experimental/camunda-zeebe-worker-symbol-processor` | `kora-java-camunda-zeebe-worker` |
| GraalVM native image | — (build configuration, not a Kora module) | `examples/graalvm/kora-java-graalvm-crud-jdbc`, `kora-java-graalvm-crud-cassandra`, `kora-java-graalvm-kafka` |
| JMS | `jms` | — |
| Konvert mapping | `mapping/konvert-ksp-extension` | — |
| Netty tuning | `netty-common` | — |
| Redis client (outside caching) | `redis/redis-lettuce` | `kora-java-cache-redis` |
| Probes / readiness | `core/common/.../liveness`, `core/common/.../readiness`, `http/http-server-common/.../system` | any example with `httpServer.system` configured |

**Driver guidance:** JDBC with HikariCP is the only relational path in Kora 2.0, and it is the
maintainer-recommended one. Virtual threads make blocking JDBC calls cheap, which is why the
reactive drivers were dropped rather than ported.
