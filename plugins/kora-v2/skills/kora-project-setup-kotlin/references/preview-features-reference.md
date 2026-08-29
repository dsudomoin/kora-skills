# Enabling JDK preview features (Java StructuredTaskScope) in a Kotlin Kora build

Kora 2.x contracts are synchronous and run on virtual threads. Kotlin structured
concurrency — `coroutineScope`, `supervisorScope`, `async`/`await`, `awaitAll`,
child `launch`, structured cancellation, `withTimeout` — has no Kora 2.x
contract. Real in-request parallelism moves to Java `StructuredTaskScope`, which
is still a **preview** API.

This file covers only the **build wiring**. It deliberately does not print a
`StructuredTaskScope` API shape; see [why](#never-copy-an-api-shape-from-another-jdk).

## Only if you actually need it

Most services never do. `suspend` that existed purely to avoid blocking on a
database or HTTP call becomes a plain synchronous call — a virtual thread
absorbs the block, and neither `Dispatchers.IO` nor `runBlocking` buys anything
back. Reach for `StructuredTaskScope` only for genuine fan-out: several
independent calls whose results are combined.

If nothing in the service forks work, do not enable preview, and drop
`kotlinx-coroutines-*` from the build once a repository-wide search finds no
remaining usages.

## Pick the JDK first

Take the **latest GA feature release** from `openjdk.org/projects/jdk` at the
time you set the project up, and use the Structured Concurrency preview
iteration shipped in *that* release. Do not run production on an EA build just
to get a newer preview API. Kora 2.x artifacts have a bytecode floor of 25, so
the choice is 25 or newer.

## Preview must be enabled everywhere

One flag on one compile task is not enough. Preview bytecode is rejected by any
JVM launch that did not opt in, and by any JVM of a different major version.

```kotlin
val latestJava = 25 // set to the latest GA feature release at setup time

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(latestJava))
}

kotlin {
    jvmToolchain(latestJava)
    compilerOptions {
        // Kotlin sources use the selected JDK API and emit preview bytecode too.
        freeCompilerArgs.addAll(
            "-Xjdk-release=$latestJava",
            "-Xjvm-enable-preview",
        )
    }
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(latestJava)
    options.compilerArgs.add("--enable-preview")
}

tasks.withType<Test>().configureEach {
    jvmArgs("--enable-preview")
}

tasks.withType<JavaExec>().configureEach {
    jvmArgs("--enable-preview")
}

application {
    // Merge with the existing arguments instead of dropping application-specific flags.
    applicationDefaultJvmArgs = listOf("--enable-preview", "-Dfile.encoding=UTF-8")
}
```

`--release` must equal the chosen JDK's major version. For a direct launch:
`java --enable-preview -jar application.jar`. The container image and any
Kubernetes command must use the same major JDK and the same flag.

Note that `--enable-preview` is not a general Kora 2.x requirement. Kora passes
it in its own test task, and the published BOM sets it in the Maven surefire
`argLine`; consumers need it only when they themselves use a preview API.

## Never copy an API shape from another JDK

The `StructuredTaskScope` preview API changed across JDK 21–25 and continues to
change: the factory (`open` vs the older `new`), the `Joiner` types, and the
return type of `join()` have all moved. Copying a snippet written for an older
JDK produces compile errors that look like a missing dependency.

Open the JEP and the Javadoc **of the JDK you selected** and re-derive:

- the scope factory and how a policy is supplied,
- the `Joiner` for your semantics — fail-fast with sibling cancellation, first
  successful result, or collect-all with per-`Subtask` `state()`/`exception()`
  inspection for supervisor-like behaviour,
- where a timeout is configured (it is scope configuration, not `withTimeout`),
- the exception types propagated out of `join()`.

## Checklist before merging

1. Confirm the latest GA release and update the toolchain, CI images and the
   container runtime together.
2. Re-read the Structured Concurrency JEP and Javadoc for that exact JDK.
3. Compile main and test, Kotlin and Java, with preview enabled.
4. Run unit and integration tests with preview enabled.
5. Verify `--enable-preview` is present in the production launcher, the
   Docker/Kubernetes command and IDE run configurations.
6. Re-test cancellation and exception propagation — they do not carry over from
   the coroutine version, and no automated migration can infer the policy.
7. Remove `kotlinx-coroutines-*` only once a repository-wide audit finds no
   remaining usages.
