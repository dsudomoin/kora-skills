# Graceful Shutdown Reference (JDK scheduler)

How `io.koraframework:scheduling-jdk` actually stops jobs in Kora 2.0, and what a job must do to be a
good citizen during shutdown.

For Quartz jobs (`scheduling.quartz.waitForJobComplete`) see the sibling skill
[kora-aop-scheduling-quartz](../../kora-aop-scheduling-quartz/SKILL.md).

## Contents

- [The shutdown path](#the-shutdown-path)
- [Why the 1.x interrupt advice no longer applies](#why-the-1x-interrupt-advice-no-longer-applies)
- [What `shutdownWait` bounds](#what-shutdownwait-bounds)
- [Cooperative cancellation patterns](#cooperative-cancellation-patterns)
- [Resource cleanup](#resource-cleanup)
- [Choosing a work-unit size](#choosing-a-work-unit-size)
- [Complete example](#complete-example)
- [Troubleshooting](#troubleshooting)

---

## The shutdown path

Kora releases the graph in reverse dependency order: every node takes read locks on its dependencies
before releasing itself, so a dependency can only be released after all its dependents are done. Each
scheduled job depends on the executor, so the order is fixed:

1. **The job releases first.** `release()` acquires the same fair `ReentrantLock` that wraps every run
   of that job, so it **blocks until the in-flight run returns**. It then cancels the schedule with
   `cancel(false)` — the `false` means *do not interrupt the running task*.
2. **Then the executor releases.** `shutdown()`, then `awaitTermination(scheduling.jdk.shutdownWait)`,
   then `shutdownNow()` if that expires. `shutdownNow()` is the only interrupt anywhere in the path —
   and by the time it can fire, every job has already stopped.

```
SIGTERM
  └─ job.release()        → waits for the current run to return (no timeout, no interrupt)
      └─ future.cancel(false)
  └─ executor.release()   → shutdown() → awaitTermination(shutdownWait) → shutdownNow()
```

---

## Why the 1.x interrupt advice no longer applies

Kora 1.x guidance was "check `Thread.currentThread().isInterrupted()` in your loop". In 2.0 the
framework **never interrupts a running scheduled job**:

- `cancel(false)` is explicitly non-interrupting.
- The executor's `shutdownNow()` happens only after every job has already been released, i.e. after the
  runs it would have interrupted have already returned.

An `isInterrupted()` check is therefore not wrong, just inert — it will not fire during a normal
shutdown, and code that relies on it to stop will not stop.

The real consequence is the opposite of what the 1.x text implied:

> **A job body that does not return blocks shutdown indefinitely.** `shutdownWait` does not bound it.
> The process only dies when the container's own kill timeout (`SIGKILL` after
> `terminationGracePeriodSeconds` in Kubernetes, `docker stop -t`) runs out.

Keep an `isInterrupted()` / `InterruptedException` path only where you genuinely block on something
interruptible (`Thread.sleep`, `BlockingQueue.poll`, `Future.get`) — it is correct hygiene there, it is
simply not the shutdown mechanism.

---

## What `shutdownWait` bounds

```hocon
scheduling.jdk.shutdownWait = 30s   # default 30s
```

It is the executor's `awaitTermination` budget: how long the pool waits for its worker threads to
finish draining before `shutdownNow()`. Because jobs are already released and quiescent at that point,
it is normally consumed in milliseconds. If it does expire you get:

```
WARN  SchedulingJdkExecutor failed completing graceful shutdown in PT30S
```

which means a pool worker was still busy with work that no job release cancelled — in practice, tasks
submitted straight to the injected `SchedulingJdkExecutor` component rather than declared with a
scheduling annotation. A thread your job body started itself is *not* a pool worker, so it does not
produce this warning (and is not waited for at all).

> The 1.x key `scheduling.shutdownWait` is gone. It is now an unknown HOCON key: ignored silently, no
> warning, and the default 30 s applies.

---

## Cooperative cancellation patterns

Since the framework will wait rather than interrupt, the job decides how quickly shutdown can proceed.

### 1. Bound the batch — the default choice

Do a bounded slice of work per run instead of draining everything. The next run picks up where this one
left off, and shutdown never waits more than one slice.

```java
@Component
public final class OutboxPublisher {

    private static final int BATCH = 500;

    @ScheduleWithFixedDelay(config = "scheduling.jobs.outbox")
    void publishPending() {
        var batch = outbox.takePending(BATCH);   // bounded by construction
        for (var message : batch) {
            broker.publish(message);
            outbox.markSent(message.id());
        }
    }
}
```

### 2. A deadline inside the run

When the batch size is not under your control, stop on the clock.

```java
@ScheduleWithFixedDelay(config = "scheduling.jobs.import")
void importRecords() {
    var deadline = Instant.now().plus(Duration.ofSeconds(20));

    for (var record : source.stream()) {
        if (Instant.now().isAfter(deadline)) {
            log.info("import yielding at deadline, resuming next run");
            return;
        }
        importRecord(record);
    }
}
```

Keep the deadline comfortably below the container's kill timeout and you have a hard upper bound on
shutdown latency, whatever the data looks like.

### 3. An owned stop flag, for a job that must poll

If the job genuinely has to loop, it needs a flag that a `Lifecycle` component can flip. Kora releases
the flag holder and the job in dependency order, so a job that depends on the flag is released first —
which is exactly why the flag must be flipped by something the *job* depends on.

```java
@Component
public final class ShutdownFlag implements Lifecycle {

    private volatile boolean stopping = false;

    public boolean stopping() { return stopping; }

    @Override public void init() { }
    @Override public void release() { stopping = true; }
}
```

The flag flips when `ShutdownFlag` is released, which happens **after** the jobs depending on it are
released — so on its own it does not shorten the current run. Use it for background threads a job
spawned, not as the primary mechanism. Prefer bounded batches and deadlines.

### 4. Blocking calls

Every blocking call inside a job should carry a timeout, so shutdown latency is bounded even when the
peer is unresponsive:

```java
queue.poll(5, TimeUnit.SECONDS);        // not queue.take()
future.get(10, TimeUnit.SECONDS);       // not future.get()
httpClient.get(...)                     // with a per-request timeout configured
```

---

## Resource cleanup

Nothing special applies during shutdown — because the run is allowed to finish, ordinary
try-with-resources is enough and `finally` always executes.

```java
@ScheduleAtFixedRate(config = "scheduling.jobs.report")
void buildReport() {
    try (var connection = dataSource.getConnection();
         var writer = Files.newBufferedWriter(target)) {
        writeReport(connection, writer);
    } catch (SQLException | IOException e) {
        log.error("report generation failed", e);
    }
}
```

Do **not** open a resource in one run and close it in the next — a one-shot `@ScheduleOnce` or a
cancelled schedule may mean the next run never happens.

---

## Choosing a work-unit size

| Job duration | Recommended shape |
|---|---|
| < 1 s | Nothing to do — it returns before shutdown notices |
| 1–30 s | Fine as-is; make sure blocking calls have timeouts |
| > 30 s | Bounded batch or an in-run deadline; otherwise shutdown waits for the whole run |
| Unbounded / streaming | Must not exist as a scheduled job. Bound it, or make it a `Lifecycle` component with its own thread and stop protocol |

The container-level number to stay under is the kill timeout —
`terminationGracePeriodSeconds` (Kubernetes, default 30 s) or `docker stop -t` (default 10 s). Anything
longer than that is not a graceful shutdown, it is a `SIGKILL` with extra steps.

---

## Complete example

```java
package com.example.app.jobs;

import io.koraframework.common.annotation.Component;
import io.koraframework.scheduling.jdk.annotation.ScheduleWithFixedDelay;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.time.Instant;

@Component
public final class DataImportJob {

    private static final Logger log = LoggerFactory.getLogger(DataImportJob.class);
    private static final Duration RUN_BUDGET = Duration.ofSeconds(20);
    private static final int PAGE = 200;

    private final ImportSource source;
    private final ImportSink sink;

    public DataImportJob(ImportSource source, ImportSink sink) {
        this.source = source;
        this.sink = sink;
    }

    @ScheduleWithFixedDelay(config = "scheduling.jobs.import")
    void importExternalData() {
        var deadline = Instant.now().plus(RUN_BUDGET);
        var imported = 0;

        while (Instant.now().isBefore(deadline)) {
            var page = source.nextPage(PAGE);      // bounded, resumable
            if (page.isEmpty()) {
                break;
            }
            for (var record : page) {
                sink.upsert(record);               // idempotent
            }
            imported += page.size();
        }

        log.info("imported {} records, resuming next run", imported);
    }
}
```

```hocon
scheduling.jobs.import {
  initialDelay = 30s
  delay = 5m
  telemetry.logging.enabled = true
}
```

Three properties make this shutdown-safe: the run is time-bounded, each page is idempotent so a
truncated run is not a lost run, and progress is durable in the source cursor rather than in memory.

---

## Troubleshooting

### Shutdown hangs after SIGTERM

The current run has not returned. Nothing interrupts it. Find the job in a thread dump — the scheduler
threads are named `kora-scheduler-N` — and give it a bounded batch or a deadline.

### `SchedulingJdkExecutor failed completing graceful shutdown in PT30S`

A pool worker is still busy after all jobs were released. Every annotated job has been cancelled by
then, so the work came from somewhere else — typically a component that injected
`SchedulingJdkExecutor` and submitted to it directly. Give that work its own `Lifecycle`, or raise
`scheduling.jdk.shutdownWait` if it is legitimately that long. Note that a thread a job body spawned
itself is not a pool worker: it never triggers this warning, and nothing waits for it — own its
lifecycle explicitly.

### The `isInterrupted()` check never fires

Expected — see [above](#why-the-1x-interrupt-advice-no-longer-applies). It is not the shutdown signal in
Kora 2.0.

### A run is cut off mid-way and data is inconsistent

Shutdown is not the only way a run ends — an exception does too. Make each unit of work idempotent and
commit progress incrementally; do not treat "the run completed" as a transaction boundary.

### Setting `scheduling.shutdownWait` changed nothing

That is the 1.x key. Use `scheduling.jdk.shutdownWait`. Unknown keys are ignored silently.

---

## See Also

- [scheduling-config-reference.md](scheduling-config-reference.md) — every `scheduling.*` key
- [jdk-scheduling-reference.md](jdk-scheduling-reference.md) — annotations and telemetry
- [kora-di-runtime](../../kora-di-runtime/SKILL.md) — `Lifecycle`, init/release ordering, `@Root`
