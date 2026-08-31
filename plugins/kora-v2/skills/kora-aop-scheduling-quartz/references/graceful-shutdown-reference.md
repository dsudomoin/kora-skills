# Shutdown Reference (Quartz, Kora 2.0)

What actually happens to a running Quartz job when a Kora 2.0 service stops, and how to bound a
long job given those semantics.

**Shutdown is not shared between the two Kora schedulers.** `scheduling-common` contains only
`SchedulingJobConfig`, `SchedulingModule` and the `telemetry/` package — no `Lifecycle`, no
shutdown code at all. Each backend owns its own teardown, and they differ on the one point that
decides how you write a long job. Everything on this page is Quartz-only; for the JDK scheduler
see [kora-aop-scheduling-jdk](../../kora-aop-scheduling-jdk/SKILL.md).

## Contents

- [What shutdown does](#what-shutdown-does)
- [How the JDK scheduler differs](#how-the-jdk-scheduler-differs)
- [Why `isInterrupted()` does not work here](#why-isinterrupted-does-not-work-here)
- [Configuration](#configuration)
- [Bounding a long job](#bounding-a-long-job)
- [Cooperative cancellation](#cooperative-cancellation)
- [Resumable jobs](#resumable-jobs)
- [Operational control through the Scheduler](#operational-control-through-the-scheduler)
- [Troubleshooting](#troubleshooting)
- [See also](#see-also)

---

## What shutdown does

`KoraQuartzScheduler` is a `Lifecycle`, so the graph calls `release()` on it during shutdown:

```java
// KoraQuartzScheduler.release(), simplified
final boolean waitForComplete = config.waitForJobComplete();   // scheduling.quartz.waitForJobComplete
scheduler.shutdown(waitForComplete);
```

`org.quartz.Scheduler.shutdown(boolean)` stops the scheduler thread — no new firings — and then
shuts the thread pool down. In `SimpleThreadPool`, the default pool, that means:

- each worker's run flag is cleared (`WorkerThread.shutdown()` is nothing but `run.set(false)`);
- idle workers exit after their current `wait`;
- **busy workers are left alone.** Quartz's own javadoc on `shutdown` says *"Jobs currently in
  progress will complete."*
- if `waitForJobsToComplete` is `true`, `shutdown` additionally waits for the busy workers and
  joins them.

So the only difference between the two settings is whether `release()` blocks.

---

## How the JDK scheduler differs

Worth knowing because the Kora 1.x documentation used one shutdown page for both schedulers, and
the advice it gave is correct for the JDK one and wrong for Quartz.

| | Quartz (`scheduling-quartz`) | JDK (`scheduling-jdk`) |
|---|---|---|
| Component | `KoraQuartzScheduler.release()` | `ThreadPoolSchedulingJdkExecutor.release()` |
| Call | `Scheduler.shutdown(waitForJobComplete)` | `shutdown()` → `awaitTermination(shutdownWait)` → `shutdownNow()` on timeout |
| Config key | `scheduling.quartz.waitForJobComplete` | `scheduling.jdk.shutdownWait` |
| Default | `true` | `30s` |
| Wait is bounded | **no** — `true` waits indefinitely | **yes** — capped at `shutdownWait` |
| Running job interrupted | **never** | **yes**, once `shutdownWait` elapses (`shutdownNow()`) |

So `Thread.currentThread().isInterrupted()` is a real signal on the JDK scheduler and dead code
on Quartz. That single row is why the shared 1.x guidance had to be split. It also means the
Quartz wait has no timeout of its own: bounding a long job is the application's job, not a
config knob.

---

## Why `isInterrupted()` does not work here

Kora 1.x guidance told you to poll `Thread.currentThread().isInterrupted()` in the job body. In
Kora 2.0 on Quartz 2.5.2 that check **never becomes true as a result of shutdown** — nothing in
the path interrupts the worker thread. It is dead code.

`org.quartz.InterruptableJob` plus `Scheduler.interrupt(JobKey)` is Quartz's real cancellation
mechanism, and it is not reachable from a Kora-generated job:

- the generated `$<Class>_<method>_Job` is declared `final`, so you cannot subclass it;
- it extends `KoraQuartzJob`, whose `execute(JobExecutionContext)` is `final`;
- neither implements `InterruptableJob`, so `Scheduler.interrupt(...)` has no
  `interrupt()` to call.

Two consequences to plan around:

1. A job that runs for ten minutes keeps the JVM alive for up to ten minutes at shutdown
   (`waitForJobComplete = true`), or keeps running while the rest of the graph is torn down
   underneath it (`waitForJobComplete = false`) — Quartz workers are non-daemon threads.
2. Cancellation has to be **cooperative**, driven by state your own code owns.

---

## Configuration

```hocon
scheduling.quartz.waitForJobComplete = true    # default
```

```yaml
scheduling:
  quartz:
    waitForJobComplete: true
```

| Value | `release()` | Running job |
|---|---|---|
| `true` (default) | blocks until running jobs finish | runs to completion |
| `false` | returns immediately | still runs to completion, now concurrently with graph teardown |

`false` is almost never the right answer: the job outlives the components it depends on (data
sources, HTTP clients) and will fail on already-released resources. Prefer keeping the default
and making the job short.

---

## Bounding a long job

The reliable lever is the job body. Give every long job a deadline or a batch cap so a single
execution has a known upper bound:

```java
@Component
public final class BatchJob {

    private static final Duration BUDGET = Duration.ofSeconds(30);

    @DisallowConcurrentExecution
    @ScheduleWithCron("0 */5 * * * ?")
    void processBatch() {
        var deadline = Instant.now().plus(BUDGET);
        for (var item : repository.findPending(500)) {
            if (Instant.now().isAfter(deadline)) {
                log.info("Budget exhausted, resuming on the next fire");
                return;
            }
            process(item);
        }
    }
}
```

```kotlin
@Component
class BatchJob(private val repository: PendingRepository) {

    @DisallowConcurrentExecution
    @ScheduleWithCron("0 */5 * * * ?")
    fun processBatch() {
        val deadline = Instant.now().plus(BUDGET)
        for (item in repository.findPending(500)) {
            if (Instant.now().isAfter(deadline)) {
                log.info("Budget exhausted, resuming on the next fire")
                return
            }
            process(item)
        }
    }

    private companion object {
        val BUDGET: Duration = Duration.ofSeconds(30)
    }
}
```

`@DisallowConcurrentExecution` keeps the next firing from overlapping while the previous one is
still draining the backlog.

---

## Cooperative cancellation

If a job genuinely has to react to shutdown, publish the signal yourself. A `Lifecycle`
component owns the flag and flips it in `release()`; the job reads it:

```java
// io.koraframework.application.graph.Lifecycle
@Component
public final class ShutdownSignal implements Lifecycle {

    private final AtomicBoolean stopping = new AtomicBoolean(false);

    public boolean isStopping() {
        return stopping.get();
    }

    @Override
    public void init() { }

    @Override
    public void release() {
        stopping.set(true);
    }
}
```

```java
@Component
public final class BatchJob {

    private final ShutdownSignal shutdown;

    BatchJob(ShutdownSignal shutdown) {
        this.shutdown = shutdown;
    }

    @DisallowConcurrentExecution
    @ScheduleWithCron("0 */5 * * * ?")
    void processBatch() {
        for (var item : items) {
            if (shutdown.isStopping()) {
                log.info("Shutdown requested, stopping after {} items", processed);
                return;
            }
            process(item);
        }
    }
}
```

Mind the release order: the graph releases in reverse initialisation order, so a component the
job **depends on** is released *after* `KoraQuartzScheduler`. With
`waitForJobComplete = true` that is too late to help — the scheduler's `release()` is already
blocking on the job. Keep this pattern for the `waitForJobComplete = false` case, or drive the
flag from something released early (an HTTP admin endpoint, a readiness probe flip, a
`Runtime.getRuntime().addShutdownHook` you install yourself). A deadline in the job body is
simpler and always works — reach for that first.

---

## Resumable jobs

Because a job can be cut short by its own budget, or the process can die mid-run, make progress
durable rather than in-memory:

```java
@Component
public final class ResumableJob {

    @PersistJobDataAfterExecution     // needs a JDBC JobStore to survive a restart
    @DisallowConcurrentExecution
    @ScheduleWithCron("0 */10 * * * ?")
    void process(JobExecutionContext ctx) {
        var data = ctx.getJobDetail().getJobDataMap();
        long cursor = data.getLong("cursor");

        var deadline = Instant.now().plusSeconds(30);
        for (var item : repository.findAfter(cursor, 500)) {
            if (Instant.now().isAfter(deadline)) break;
            process(item);
            cursor = item.id();
        }
        data.put("cursor", cursor);
    }
}
```

`@PersistJobDataAfterExecution` only survives a restart with a persistent job store — with the
default `RAMJobStore` the map is in-memory. See
[scheduling-config-reference.md](scheduling-config-reference.md#persistence-jdbc-jobstore).
A cursor in your own database works regardless of job store and is usually the better choice.

---

## Operational control through the `Scheduler`

`KoraQuartzScheduler` is `Wrapped<org.quartz.Scheduler>`, so `org.quartz.Scheduler` is
injectable. That is the supported way to pause or fire jobs on demand:

```java
@Component
public final class SchedulerAdmin {

    private final Scheduler scheduler;

    SchedulerAdmin(Scheduler scheduler) {
        this.scheduler = scheduler;
    }

    public void drain() throws SchedulerException {
        scheduler.standby();          // stop firing, keep the scheduler alive
    }

    public void resume() throws SchedulerException {
        scheduler.start();
    }
}
```

`standby()` stops new firings without touching jobs already running — the same asymmetry as
shutdown.

---

## Troubleshooting

### Shutdown hangs

**Cause:** `waitForJobComplete` is `true` (the default) and a job is mid-run. There is no
timeout on that wait.
**Fix:** bound the job body with a deadline or batch cap. Do not switch to `false` — that only
hides the wait while the job keeps running against a released graph.

### The job keeps running after shutdown "completed"

**Cause:** `waitForJobComplete = false`. Nothing was cancelled; the non-daemon Quartz worker is
still executing.
**Fix:** return to `true` and bound the job body.

### `isInterrupted()` never returns `true`

Expected. Nothing interrupts a Quartz worker — see
[above](#why-isinterrupted-does-not-work-here). Use a deadline or a cooperative flag.

### A job restarted mid-batch reprocesses items

**Cause:** progress was only in memory, or in a `JobDataMap` backed by `RAMJobStore`.
**Fix:** persist the cursor in your own database, and make each unit idempotent.

### Errors on shutdown from inside the job

**Cause:** `waitForJobComplete = false` let the job outlive its dependencies.
**Fix:** `waitForJobComplete = true` plus a bounded body.

---

## See also

- [quartz-scheduling-reference.md](quartz-scheduling-reference.md) — annotations and generated code
- [scheduling-config-reference.md](scheduling-config-reference.md) — `scheduling.quartz.*` keys
- [kora-di-runtime](../../kora-di-runtime/SKILL.md) — `Lifecycle`, `@Root` and graph release order
