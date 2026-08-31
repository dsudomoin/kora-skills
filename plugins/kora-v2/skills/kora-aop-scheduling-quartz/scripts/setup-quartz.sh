#!/usr/bin/env bash
# Scaffold Kora 2.0 Quartz scheduling in a project:
#   - adds io.koraframework:scheduling-quartz to the Gradle build
#   - writes a QuartzJobs source file
#   - writes / extends application.conf with the 2.0 `scheduling.quartz.*` keys
#
# Kora 2.0 facts baked in (verified against scheduling-quartz at tag 2.0.0.RC1):
#   module      io.koraframework.scheduling.quartz.QuartzModule
#   annotations io.koraframework.scheduling.quartz.{ScheduleWithCron,ScheduleWithTrigger,
#               DisallowConcurrentExecution,PersistJobDataAfterExecution}
#   config      scheduling.quartz.properties / scheduling.quartz.waitForJobComplete
#               scheduling.telemetry.{logging,metrics,tracing}.enabled
#
# Idempotent: re-running never duplicates a dependency, a source file or a config block.

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: setup-quartz.sh [options] [project-root]

Options:
  -n, --dry-run          Print every change without touching the filesystem
  -l, --lang LANG        java (default) or kotlin
  -p, --package PKG      Base package for the generated jobs class
                         (default: com.example.app.jobs)
  -h, --help             Show this help

Arguments:
  project-root           Defaults to the current directory

Examples:
  ./setup-quartz.sh --dry-run
  ./setup-quartz.sh --lang kotlin --package com.acme.billing.jobs ~/projects/billing
USAGE
}

DRY_RUN=0
LANG_KIND="java"
PKG="com.example.app.jobs"
PROJECT_ROOT=""

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1; shift ;;
        -l|--lang)    LANG_KIND="${2:-}"; shift 2 ;;
        -p|--package) PKG="${2:-}"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        -*)           echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)            PROJECT_ROOT="$1"; shift ;;
    esac
done

PROJECT_ROOT="${PROJECT_ROOT:-.}"

case "$LANG_KIND" in
    java|kotlin) ;;
    *) echo "Error: --lang must be 'java' or 'kotlin', got '$LANG_KIND'" >&2; exit 2 ;;
esac

if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: project root '$PROJECT_ROOT' does not exist" >&2
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN — no files will be written"
fi
echo "Kora 2.0 Quartz setup in $PROJECT_ROOT (lang=$LANG_KIND, package=$PKG)"
echo

# --- helpers ---------------------------------------------------------------

# write_file <path> <<<"content" — honours DRY_RUN, never clobbers an existing file
write_file() {
    local path="$1" content
    content="$(cat)"
    if [ -f "$path" ]; then
        echo "  = $path already exists, left untouched"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + would create $path"
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" > "$path"
    echo "  + created $path"
}

append_file() {
    local path="$1" content
    content="$(cat)"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + would append to $path"
        return 0
    fi
    printf '%s\n' "$content" >> "$path"
    echo "  + appended to $path"
}

# --- 1. Gradle dependency --------------------------------------------------

BUILD_FILE=""
for candidate in "$PROJECT_ROOT/build.gradle.kts" "$PROJECT_ROOT/build.gradle"; do
    [ -f "$candidate" ] && { BUILD_FILE="$candidate"; break; }
done

if [ -z "$BUILD_FILE" ]; then
    echo "  ! no build.gradle.kts or build.gradle in $PROJECT_ROOT — add manually:"
    echo "      implementation \"io.koraframework:scheduling-quartz\""
elif grep -q 'scheduling-quartz' "$BUILD_FILE"; then
    echo "  = scheduling-quartz already declared in $(basename "$BUILD_FILE")"
else
    case "$BUILD_FILE" in
        *.kts) DEP_LINE='    implementation("io.koraframework:scheduling-quartz")' ;;
        *)     DEP_LINE='    implementation "io.koraframework:scheduling-quartz"' ;;
    esac
    if grep -qE '^[[:space:]]*dependencies[[:space:]]*\{' "$BUILD_FILE"; then
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "  + would insert into $(basename "$BUILD_FILE"): ${DEP_LINE# }"
        else
            TMP="$(mktemp)"
            awk -v dep="$DEP_LINE" '
                !done && /^[[:space:]]*dependencies[[:space:]]*\{/ { print; print dep; done = 1; next }
                { print }
            ' "$BUILD_FILE" > "$TMP"
            mv "$TMP" "$BUILD_FILE"
            echo "  + added scheduling-quartz to $(basename "$BUILD_FILE")"
        fi
    else
        echo "  ! no dependencies { } block in $(basename "$BUILD_FILE") — add manually:"
        echo "      ${DEP_LINE# }"
    fi
fi

if ! grep -qE 'annotation-processors|symbol-processors' "${BUILD_FILE:-/dev/null}" 2>/dev/null; then
    echo "  ! the Kora processor is missing — nothing is generated without it:"
    if [ "$LANG_KIND" = "kotlin" ]; then
        echo '      ksp("io.koraframework:symbol-processors")'
    else
        echo '      annotationProcessor "io.koraframework:annotation-processors"'
    fi
fi

# --- 2. jobs source --------------------------------------------------------

PKG_PATH="${PKG//.//}"
if [ "$LANG_KIND" = "kotlin" ]; then
    JOBS_FILE="$PROJECT_ROOT/src/main/kotlin/$PKG_PATH/QuartzJobs.kt"
    write_file "$JOBS_FILE" <<EOF
package $PKG

import io.koraframework.common.annotation.Component
import io.koraframework.scheduling.quartz.DisallowConcurrentExecution
import io.koraframework.scheduling.quartz.ScheduleWithCron
import org.slf4j.LoggerFactory

/**
 * Kora 2.0 Quartz jobs.
 *
 * The class need not be \`open\`: the generated \$QuartzJobs_<method>_Job wrapper calls this
 * component directly rather than proxying it. Scheduled functions must not be \`suspend\`.
 */
@Component
class QuartzJobs {

    /** Daily at 03:00; @DisallowConcurrentExecution stops overlapping runs. */
    @DisallowConcurrentExecution
    @ScheduleWithCron("0 0 3 * * ?")
    fun nightlyReport() {
        log.info("Generating nightly report")
    }

    /** Cron taken from \`jobs.hourly\` in application.conf. */
    @ScheduleWithCron(config = "jobs.hourly")
    fun hourlyCheck() {
        log.info("Running hourly check")
    }

    private companion object {
        private val log = LoggerFactory.getLogger(QuartzJobs::class.java)
    }
}
EOF
else
    JOBS_FILE="$PROJECT_ROOT/src/main/java/$PKG_PATH/QuartzJobs.java"
    write_file "$JOBS_FILE" <<EOF
package $PKG;

import io.koraframework.common.annotation.Component;
import io.koraframework.scheduling.quartz.DisallowConcurrentExecution;
import io.koraframework.scheduling.quartz.ScheduleWithCron;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Kora 2.0 Quartz jobs.
 *
 * The class may stay final: the generated \$QuartzJobs_<method>_Job wrapper calls this
 * component directly rather than proxying it.
 */
@Component
public final class QuartzJobs {

    private static final Logger log = LoggerFactory.getLogger(QuartzJobs.class);

    /** Daily at 03:00; @DisallowConcurrentExecution stops overlapping runs. */
    @DisallowConcurrentExecution
    @ScheduleWithCron("0 0 3 * * ?")
    void nightlyReport() {
        log.info("Generating nightly report");
    }

    /** Cron taken from \`jobs.hourly\` in application.conf. */
    @ScheduleWithCron(config = "jobs.hourly")
    void hourlyCheck() {
        log.info("Running hourly check");
    }
}
EOF
fi

# --- 3. configuration ------------------------------------------------------

CONF_FILE="$PROJECT_ROOT/src/main/resources/application.conf"

read -r -d '' QUARTZ_CONF <<'EOF' || true
scheduling {
  quartz {
    # raw org.quartz.* properties handed to StdSchedulerFactory
    properties {
      "org.quartz.threadPool.threadCount" = "10"
    }
    # default true: shutdown blocks until running jobs finish.
    # Quartz never interrupts a running job, so bound long job bodies yourself.
    waitForJobComplete = true
  }

  telemetry {
    logging.enabled = true    # default false
    metrics.enabled = true    # default false, also needs a MeterRegistry in the graph
    tracing.enabled = true    # default true, also needs a Tracer in the graph
  }
}

jobs {
  hourly {
    cron = "0 0 * * * ?"
  }
}
EOF

if [ ! -f "$CONF_FILE" ]; then
    write_file "$CONF_FILE" <<EOF
$QUARTZ_CONF
EOF
elif grep -qE '^[[:space:]]*scheduling[[:space:]]*\{|scheduling\.quartz' "$CONF_FILE"; then
    echo "  = scheduling config already present in application.conf"
else
    append_file "$CONF_FILE" <<EOF

$QUARTZ_CONF
EOF
fi

# --- 4. stale Kora 1.x keys ------------------------------------------------

if [ -f "$CONF_FILE" ]; then
    # A top-level `quartz { }` is the Kora 1.x key. A nested `scheduling { quartz { } }`
    # is the 2.0 layout, so only flag the block when nothing encloses it.
    ROOT_QUARTZ="$(awk '
        { line = $0; sub(/#.*/, "", line) }
        match(line, /^[[:space:]]*[A-Za-z0-9_."-]+[[:space:]]*\{/) {
            name = line
            sub(/^[[:space:]]*/, "", name)
            sub(/[[:space:]]*\{.*$/, "", name)
            gsub(/"/, "", name)
            depth++
            if (depth == 1 && name == "quartz") print "found"
        }
        line ~ /}/ { if (depth > 0) depth-- }
    ' "$CONF_FILE")"
    if [ -n "$ROOT_QUARTZ" ]; then
        echo "  ! application.conf has a top-level 'quartz { }' block — that is a Kora 1.x key."
        echo "    Kora 2.0 reads Quartz properties from 'scheduling.quartz.properties'."
        echo "    The stale block is ignored silently; move its contents."
    fi
    # Resolve the enclosing HOCON path of every waitForJobComplete occurrence, so a nearby
    # but unrelated `quartz { }` block does not mask a stale scheduling.waitForJobComplete.
    STALE_WAIT="$(awk '
        { line = $0; sub(/#.*/, "", line) }
        match(line, /^[[:space:]]*[A-Za-z0-9_."-]+[[:space:]]*\{/) {
            name = line
            sub(/^[[:space:]]*/, "", name)
            sub(/[[:space:]]*\{.*$/, "", name)
            gsub(/"/, "", name)
            stack[++depth] = name
        }
        line ~ /waitForJobComplete/ {
            path = ""
            for (i = 1; i <= depth; i++) path = path (i > 1 ? "." : "") stack[i]
            full = (path == "" ? "" : path ".") "waitForJobComplete"
            if (full !~ /quartz/ && line !~ /quartz/) print full
        }
        line ~ /}/ { if (depth > 0) depth-- }
    ' "$CONF_FILE")"
    if [ -n "$STALE_WAIT" ]; then
        echo "  ! stale shutdown key(s) in application.conf: $(tr '\n' ' ' <<<"$STALE_WAIT")"
        echo "    Kora 1.x read 'scheduling.waitForJobComplete'; Kora 2.0 reads"
        echo "    'scheduling.quartz.waitForJobComplete' (default true) and ignores the old key."
    fi
fi

echo
echo "Next steps:"
echo "  1. Add QuartzModule to your @KoraApp interface:"
if [ "$LANG_KIND" = "kotlin" ]; then
    echo "       import io.koraframework.scheduling.quartz.QuartzModule"
    echo "       @KoraApp interface Application : HoconConfigModule, QuartzModule"
else
    echo "       import io.koraframework.scheduling.quartz.QuartzModule;"
    echo "       @KoraApp public interface Application extends HoconConfigModule, QuartzModule { }"
fi
echo "  2. Review $JOBS_FILE"
echo "  3. For a custom trigger, declare a Trigger tagged with your job class on the @KoraApp"
if [ "$LANG_KIND" = "kotlin" ]; then
    echo "     interface (@Tag(YourJob::class)) and use @ScheduleWithTrigger(YourJob::class)"
else
    echo "     interface (@Tag(YourJob.class)) and use @ScheduleWithTrigger(YourJob.class)"
fi
echo "     — the class tag is passed directly, the Kora 1.x nested @Tag form no longer compiles."
