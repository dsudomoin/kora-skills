# Docker Images for Kora 2.0 Applications

Building and running a packaged Kora 2.0 application in Docker, for black-box tests and for
deployment.

**JDK 25 is a hard floor.** `kora-bom` declares `java.version = 25` and the published artifacts are
class-file major 69, so a `21-jre` base image fails at startup with `UnsupportedClassVersionError`
before Kora logs anything.

## Contents

1. [Runtime Dockerfile over a prebuilt archive](#runtime-dockerfile-over-a-prebuilt-archive)
2. [`distTar` vs `installDist`](#disttar-vs-installdist)
3. [Multi-stage Dockerfile](#multi-stage-dockerfile)
4. [GraalVM native-image Dockerfile](#graalvm-native-image-dockerfile)
5. [Choosing between them](#choosing-between-them)
6. [Reusing a prebuilt image in tests](#reusing-a-prebuilt-image-in-tests)
7. [CI/CD](#cicd)
8. [`.dockerignore`](#dockerignore)
9. [Verifying an image by hand](#verifying-an-image-by-hand)

---

## Runtime Dockerfile over a prebuilt archive

This is the shape the migrated Kora 2.0 examples ship, and the one `ImageFromDockerfile` builds in
a black-box test. It packages the archive Gradle already produced; it does not build anything.

```dockerfile
FROM eclipse-temurin:25-jre-jammy

ARG TARGET_DIR=/opt/app

COPY build/distributions/application.tar /application.tar
RUN mkdir -p ${TARGET_DIR}
RUN tar -xf /application.tar -C ${TARGET_DIR}
RUN rm /application.tar

ARG DOCKER_USER=app
RUN groupadd -r ${DOCKER_USER} && useradd -rg ${DOCKER_USER} ${DOCKER_USER}
USER ${DOCKER_USER}

# 8080 public API, 8085 system API (/system/readiness, /system/liveness, /metrics)
EXPOSE 8080/tcp
EXPOSE 8085/tcp
CMD ["/opt/app/application/bin/application"]
```

It expects the Gradle `application` plugin configured so the archive and the launcher have stable
names:

```groovy
application {
    applicationName = "application"
    mainClass = "com.example.Application"
}
distTar { archiveFileName = "application.tar" }
```

`CMD` then points at `/opt/app/application/bin/application` — `<TARGET_DIR>/<applicationName>/bin/<applicationName>`.

### Building and running it

```bash
./gradlew distTar
docker build -t myapp:1.0.0 .
docker run -p 8080:8080 -p 8085:8085 myapp:1.0.0
curl -f http://localhost:8085/system/readiness
```

---

## `distTar` vs `installDist`

They are different tasks with different outputs, and a Dockerfile can only consume one of them:

| Task | Output |
|---|---|
| `distTar` | `build/distributions/<archiveFileName>` — a **tar archive** |
| `installDist` | `build/install/<applicationName>/` — an **exploded directory** |

`COPY build/distributions/*.tar` requires `distTar`. Running `installDist` and then building that
Dockerfile fails with `COPY failed: no source files were specified`, which reads like a Docker
problem and is really a Gradle one. In a black-box module, wire it explicitly:

```groovy
test {
    dependsOn ":my-service-app:distTar"
    inputs.file("../my-service-app/Dockerfile")
    inputs.file("../my-service-app/build/distributions/application.tar")
}
```

The `inputs.file(...)` declarations are what make Gradle re-run the tests when the image inputs
change, instead of reporting `UP-TO-DATE` against a stale archive.

---

## Multi-stage Dockerfile

Builds the application inside Docker, so the host needs no JDK or Gradle. The build stage must also
be JDK 25 — Kora's annotation processor and KSP run against the Kora artifacts, which are Java 25
bytecode.

> The migrated Kora 2.0 examples do **not** build inside Docker: they build with Gradle on the host
> and the image only packages the result. Treat this variant as a convenience, and check the
> builder image tag against your registry before relying on it.

```dockerfile
# syntax=docker/dockerfile:1

ARG BUILD_IMAGE=eclipse-temurin:25-jdk-jammy
ARG RUN_IMAGE=eclipse-temurin:25-jre-jammy

FROM ${BUILD_IMAGE} AS build
WORKDIR /src

# copy the wrapper and build scripts first so the dependency layer caches
COPY gradlew gradlew.bat ./
COPY gradle/ ./gradle/
COPY settings.gradle* build.gradle* gradle.properties* ./
RUN ./gradlew --no-daemon dependencies || true

COPY . .
RUN ./gradlew --no-daemon distTar

FROM ${RUN_IMAGE}

ARG TARGET_DIR=/opt/app
COPY --from=build /src/build/distributions/*.tar /application.tar
RUN mkdir -p ${TARGET_DIR} \
 && tar -xf /application.tar -C ${TARGET_DIR} \
 && rm /application.tar

ARG DOCKER_USER=app
RUN groupadd -r ${DOCKER_USER} && useradd -rg ${DOCKER_USER} ${DOCKER_USER}
USER ${DOCKER_USER}

EXPOSE 8080/tcp
EXPOSE 8085/tcp
CMD ["/opt/app/application/bin/application"]
```

Kotlin needs the same JDK 25 build stage — nothing about Kotlin lowers the floor. The migrated
Kotlin examples set `jvmToolchain(25)` and a Java toolchain of 25 side by side, and KSP runs on the
same JVM.

### A note on Alpine

`eclipse-temurin:*-jre-alpine` images are much smaller but use musl libc. Native components —
some JDBC drivers, compression and crypto libraries, GraalVM output — can behave differently there.
The migrated examples all use the glibc `jammy` images. If you switch to Alpine, the black-box
suite is exactly the test that will tell you whether it worked.

---

## GraalVM native-image Dockerfile

This is what the migrated GraalVM examples build, and what their black-box tests run through the
same `AppContainer`:

```dockerfile
FROM ghcr.io/graalvm/native-image-community:25 AS builder

ARG TARGET_DIR=/opt/app
ARG SOURCE_DIR=build/libs
WORKDIR $TARGET_DIR

COPY $SOURCE_DIR/*-all.jar $TARGET_DIR/application.jar

RUN native-image --no-fallback -classpath $TARGET_DIR/application.jar

FROM ubuntu:noble-20240212 AS runner

ARG TARGET_DIR=/opt/app
WORKDIR $TARGET_DIR

COPY --from=builder $TARGET_DIR/application $TARGET_DIR/application

ARG DOCKER_USER=app
RUN groupadd -r $DOCKER_USER && useradd -rg $DOCKER_USER $DOCKER_USER
RUN chmod +x application
USER $DOCKER_USER

EXPOSE 8080/tcp
EXPOSE 8085/tcp
CMD "/opt/app/application"
```

The builder stage consumes a **fat jar** (`*-all.jar`, produced by the Shadow plugin), not the
`distTar` archive. On the Gradle side that means `assemble.dependsOn shadowJar` and, for the
`nativeCompile` task, the plain `jar` task must stay **enabled** — `nativeCompile` builds its
classpath from the project's own artifacts, so the 1.x habit of `jar.enabled = false` breaks it.

Two things about this stage decide whether the image works at all:

- **Metadata file names are load-bearing.** Only `reflect-config.json`, `resource-config.json`,
  `proxy-config.json`, `serialization-config.json`, `jni-config.json`, `native-image.properties`
  and `reachability-metadata.json` under `META-INF/native-image/<group>/` are read.
  `reflection-config.json` — with the extra `ion` — is ignored silently and the build still
  succeeds. Renaming the group directory during a package migration is the moment this breaks.
- **A green build is not a passing test.** Run the five-point acceptance set from
  [blackbox-integration-reference.md](blackbox-integration-reference.md#native-image-acceptance):
  the binary survives, `/system/readiness` is 200, `/metrics` returns real series, the scenario runs
  against a real dependency, and the startup log has no stack traces.

Native containers start more slowly than JVM ones; the migrated native examples allow 50–60 s of
startup timeout against 30 s for the JVM image.

---

## Choosing between them

| | Runtime-only | Multi-stage | GraalVM native |
|---|---|---|---|
| JDK/Gradle needed on host | yes | no | yes (for the fat jar) |
| Build time | fastest | slower | slowest by far |
| Image size | ~JRE + app | ~JRE + app | smallest |
| Startup | JVM | JVM | fastest |
| Risk profile | packaging only | packaging only | **runtime metadata defects that build green** |
| Used by the migrated examples | yes | no | yes |

---

## Reusing a prebuilt image in tests

Building the image inside the test is convenient locally and wasteful in CI, where the image is
usually built and pushed by an earlier job. Branch the container construction on an environment
variable and keep everything else identical:

```java
public static AppContainer build() {
    var appImage = System.getenv("APP_IMAGE");
    return (appImage != null && !appImage.isBlank())
            ? new AppContainer(DockerImageName.parse(appImage))
            : new AppContainer();   // ImageFromDockerfile(...)
}
```

The exposed ports and the `/system/readiness` wait must not differ between the branches — if they
do, CI stops testing what you debugged locally.

---

## CI/CD

### GitHub Actions

```yaml
name: Build and Test

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          java-version: '25'
          distribution: 'temurin'

      - name: Build distribution
        run: ./gradlew distTar

      - name: Build image
        run: docker build -t myapp:${{ github.sha }} .

      - name: Black-box tests against the prebuilt image
        run: ./gradlew test
        env:
          APP_IMAGE: myapp:${{ github.sha }}

      - name: Push
        if: github.ref == 'refs/heads/main'
        run: |
          docker tag myapp:${{ github.sha }} registry.example.com/myapp:latest
          docker push registry.example.com/myapp:latest
```

Two things bite in CI specifically:

- **The Gradle JVM, not just the toolchain, must be 25+.** `io.koraframework:openapi-generator`
  lands on the buildscript classpath, which is resolved by the JVM running Gradle itself. On JDK 21
  configuration fails with `Dependency requires at least JVM runtime version 25. This build uses a
  Java 21 JVM.` A `java { toolchain { ... } }` block does not fix that.
- **Testcontainers needs a Docker daemon in the test job.** Docker-in-Docker or a mounted socket;
  a plain JDK runner will fail at container start, not at compile time.

---

## `.dockerignore`

Keeps the build context small and, more importantly, keeps stale local `build/` output from being
copied into a multi-stage build.

```gitignore
.gradle/
build/
!build/distributions/
!gradle/wrapper/

.idea/
*.iml
.vscode/
.DS_Store

*.log
logs/

application-local.conf
*.local
.env

Dockerfile*
.dockerignore
docker-compose*.yml

*.md
docs/
.git/
```

The `!build/distributions/` re-inclusion is required by the runtime-only Dockerfile, which copies
the archive out of the context. Drop it for the multi-stage variant, which builds its own.

---

## Verifying an image by hand

Before wiring a new image into a black-box suite, confirm the two things the test depends on:

```bash
docker inspect myapp:1.0.0 --format '{{json .Config.ExposedPorts}}'
# expect both 8080/tcp and 8085/tcp

docker run -d --name kora-check -p 8080:8080 -p 8085:8085 myapp:1.0.0
curl -sf http://localhost:8085/system/liveness   && echo "liveness ok"
curl -sf http://localhost:8085/system/readiness  && echo "readiness ok"
curl -s  http://localhost:8085/metrics | head -5
docker logs kora-check | grep -i exception
docker rm -f kora-check
```

If `/metrics` answers `# Metric Scraper disabled`, the application has no `MetricsScraper` in its
graph — add `io.koraframework:micrometer-module`. If it answers with JVM series only, component
metrics are still at their default `false`; set `httpServer.telemetry.metrics.enabled = true`.

---

## Related

- [blackbox-integration-reference.md](blackbox-integration-reference.md) — the tests that run these images
- [testcontainers-reference.md](testcontainers-reference.md) — Testcontainers coordinates and API
- [docker-compose-reference.md](docker-compose-reference.md) — Compose as a local/CI environment
