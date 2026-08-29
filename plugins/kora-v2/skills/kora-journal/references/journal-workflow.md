# Kora Journal Workflow

**Journal Location:** `~/.kora-journal/<project>/<module>/<YYYY-MM-DD>_slug.md`

**Format:** Each entry is a **separate Markdown file** for easy management.

**CLI:** `python3 skills/kora-journal/scripts/kora_journal.py <command>` — the path is relative to
the skill package (`../kora-journal/scripts/kora_journal.py` from the `kora-starter` mirror), and
the command must be run with the **project** as the working directory, because project and module
are derived from `cwd`.

---

## Overview

Continuous-improvement journal for the Kora skills — a shared store for collecting mistakes made
against the Kora Framework and folding the fixes back into the skills.

> **⚠️ Important:** the journal records **incorrect Kora usage ONLY**. Not application business logic.

The store is shared with the Kora 1.x package on purpose. New entries carry `kora: "2.x"` in their
front matter; an entry without that field is older, may describe a 1.x API, and must be verified
against the Kora 2.0 source before it is applied.

---

## Workflow

### 1. Search Before Implementing

This is step 3 of the meta-skill's per-task procedure, and the reason the journal exists.

```bash
# From the project directory
python3 skills/kora-journal/scripts/kora_journal.py search "http interceptor auth" --limit 5

# Tag-only search — precise, uses the fixed vocabulary
python3 skills/kora-journal/scripts/kora_journal.py search "kora1x migration" --by-tags
```

`search` covers the **whole store** by default (`--scope all`), so a mistake recorded in another
service is still found. Hits from other projects are printed as `<project>/<module>/<file>.md`, and
`integrate` accepts that form verbatim.

Every hit prints its Kora line. `Kora line: unset` means the entry predates the stamp — re-check it
against the 2.0 source before applying.

---

### 2. Adding an Entry (During Session)

```bash
# From any Kora project directory
python3 skills/kora-journal/scripts/kora_journal.py add "Title" \
  --context "What you were doing" \
  --problem "What went wrong / was unclear" \
  --solution "How you fixed it" \
  --files skills/kora-xxx/SKILL.md
```

**Creates:** `~/.kora-journal/<project>/<module>/YYYY-MM-DD_slug.md`

Use the CLI, never a hand-written file: `add` owns the filename slug, the YAML front matter, the
`kora:` stamp and the tag extraction. A file dropped into the store by hand is found by content
search at best, and by `--by-tags` not at all.

**Triggers (Kora-specific):**
- ✅ Used the wrong Kora annotation, tag, or config key
- ✅ Invented a Kora API that does not exist
- ✅ Carried a Kora 1.x API into a 2.0 project (`ru.tinkoff.kora.*`, `kora-parent`, `json-module`,
  reactive/suspend contracts, or a 1.x name that still compiles such as `@Tag(HttpServerModule.class)`)
- ✅ Misapplied a Kora pattern (DI, AOP, config, telemetry)
- ✅ Found a sub-skill's documentation wrong, stale, or unclear
- ✅ Discovered a working Kora workaround worth repeating

**Do NOT record:**
- ❌ Application business logic
- ❌ Domain-specific rules
- ❌ Temporary project-specific fixes
- ❌ Anything already correct

**What makes an entry worth writing:** in Kora 2.0 a green build proves very little. Ports,
interceptor tags, telemetry switches, circuit-breaker windows and mapper wiring all fail silently
at runtime. Those failures are the ones nobody reconstructs from memory, so they are the ones that
belong here.

---

### 3. Viewing Entries

```bash
# Last 10 pending entries of the current module
python3 skills/kora-journal/scripts/kora_journal.py list --limit 10 --status pending

# All entries of the current module
python3 skills/kora-journal/scripts/kora_journal.py list

# Everything, every project
python3 skills/kora-journal/scripts/kora_journal.py list --scope all

# Only integrated entries
python3 skills/kora-journal/scripts/kora_journal.py list --status integrated
```

---

### 4. Export for Integration

```bash
# Pending entries from a date, across the whole store (the default scope for export)
python3 skills/kora-journal/scripts/kora_journal.py export --since 2026-08-01 --status pending

# Only this project
python3 skills/kora-journal/scripts/kora_journal.py export --since 2026-08-01 --scope project
```

Each exported entry is preceded by its Kora line, so a 1.x entry is visible as such while you read.

---

### 5. Apply Changes to Skills

Review the exported entries and apply them to:
- `skills/kora-xxx/SKILL.md`
- `skills/kora-xxx/references/xxx-reference.md`

Before folding an entry into a 2.x skill, confirm its claim against the Kora 2.0 source
(`.kora-agent/kora-source-2.0/` from R0). An entry is a report of what one session observed, not an
API authority — the framework source outranks it.

---

### 6. Mark as Integrated

After applying changes:

```bash
python3 skills/kora-journal/scripts/kora_journal.py integrate 2026-08-22_global-interceptor-tagged-with-httpservermodule-ne.md
```

Updates entry status: `pending` → `integrated`.

To mark as archived:
```bash
python3 skills/kora-journal/scripts/kora_journal.py integrate 2026-08-22_global-interceptor-tagged-with-httpservermodule-ne.md --status archived
```

`integrate` accepts a bare filename (resolved in the current module first, then anywhere in the
store if unique), a `<project>/<module>/<file>.md` path, or a full path inside the store. It refuses
anything outside `~/.kora-journal/` and exits non-zero when it cannot resolve the argument.

---

### 7. Journal Status

```bash
python3 skills/kora-journal/scripts/kora_journal.py status
```

Shows:
- Project name and module name as detected from `cwd`
- Store root and this module's journal directory
- The Kora line stamped into new entries
- Entry counts by status, for this module and for the whole store
- The five most recent entries across the store

---

## Storage Structure

```
~/.kora-journal/                        # Shared store: all projects, sessions, Kora packages
├── billing-service/                    # Project (git remote name, else directory name)
│   └── billing-api/                    # Module (rootProject.name, else directory name)
│       ├── 2026-08-22_global-interceptor-tagged-with-httpservermodule-ne.md
│       ├── 2026-08-21_jdbc-config-section-still-named-db.md
│       ├── 2026-08-20_openapi-client-config-path-not-lower-camel.md
│       └── ...                         # Each entry is a separate file
├── another-project/
│   └── service-module/
│       └── ...
└── ...
```

**Filename format:** `YYYY-MM-DD_slug-from-title.md`; a repeated title on the same day gets a
`_1`, `_2`, … suffix instead of overwriting the earlier entry.

**Benefits:**
- ✅ Each entry is atomic — easy to manage, move, delete
- ✅ No merge conflicts (separate files)
- ✅ Easy to export specific entries
- ✅ Status tracking per entry
- ✅ Shared across ALL sessions, projects and Kora skill packages
- ✅ Survives project deletion

**Git:** the store lives in your home directory, outside every repository — nothing to `.gitignore`.

---

## Scopes

| Scope | Reads | Default for |
|---|---|---|
| `module` | `~/.kora-journal/<project>/<module>/` | `list` |
| `project` | every module of the current project | — |
| `all` | the entire store | `search`, `export` |

`add` always writes to the current project/module — it has no `--scope`.

---

## Status Lifecycle

```
pending ──→ integrated ──→ archived
  │            │              │
  │            │              │
  └─ New entry └─ Applied to  └─ Old entries
     created      skills        (cleanup)
```

| Status | Meaning | When to Use |
|--------|---------|-------------|
| `pending` | Not yet integrated | Default for new entries |
| `integrated` | Applied to skills | After applying changes to SKILL.md |
| `archived` | Old, ready for deletion | Quarterly cleanup |

---

## Best Practices

| Practice | Why |
|----------|-----|
| **Search before implementing** | The entry that saves you was written by an earlier session, in another project |
| **Record immediately** | Don't rely on memory — add the entry right after solving |
| **Be specific** | "`@Tag(HttpServerModule.class)` compiled but the interceptor never ran" — not "fixed interceptor" |
| **Quote the real error** | `ConfigValueException: ... null at path: 'ROOT.jdbc.username'` is searchable; "config broke" is not |
| **Prefer silent failures** | A compile error self-corrects; a green build that serves the wrong behaviour does not |
| **List affected files** | Makes integration into the skills mechanical |
| **Use `--status pending`** | Focus on what has not been folded back yet |
| **Review weekly** | `list --limit 20 --status pending` |
| **Integrate monthly** | Don't let pending entries grow beyond 20 |
| **Archive quarterly** | Clean up old integrated entries |

---

## Entry File Format

Each entry is a Markdown file with YAML front matter:

```yaml
---
title: "Global interceptor tagged with HttpServerModule never ran"
date: 2026-08-22
project: billing-service
module: billing-api
author: dsudomoin
kora: "2.x"
tags: ["di", "http-server", "kora1x"]
---

# Global interceptor tagged with HttpServerModule never ran

**Date:** 2026-08-22
**Project:** billing-service
**Module:** billing-api
**Author:** dsudomoin
**Kora line:** 2.x

---

## Context

Adding an API-key auth interceptor to a Kora 2.0 HTTP server.

## Problem

Wrote `@Tag(HttpServerModule.class)` on the `HttpServerInterceptor`. It compiled and the graph
built — `HttpServerModule` still exists in 2.0 — but nothing resolves interceptors by that tag, so
every request went through unauthenticated.

## Solution

`HttpServerModule.publicHttpApiRouter` collects `@Tag(HttpServer.class) All<HttpServerInterceptor>`.
Retagged with `io.koraframework.http.server.common.HttpServer` and added a test asserting 401 on a
missing key.

## Files Affected

- `skills/kora-http-server/SKILL.md`
- `skills/kora-http-server/references/interceptors-reference.md`

---

## Metadata

- **Created:** 2026-08-22_14-30-00
- **Status:** pending  # pending → integrated → archived
- **Integrated:**
```

---

## Troubleshooting

### Journal not found

Run `status` to see the expected path. The store and the project/module directory are created by the
first `add` — read commands never create them, so an empty store simply reports that it is empty.

### Wrong project/module detected

Project name comes from the `origin` git remote, falling back to the current directory name. Module
name comes from `rootProject.name` in `settings.gradle.kts` / `settings.gradle` in the current
directory, falling back to the directory name.

Both are read from the **working directory**, so the usual cause is running the CLI from somewhere
other than the project root. `cd` to the project and re-run.

### `python: command not found`

Use `python3`. macOS and most Linux distributions do not ship a `python` executable.

### An entry I want is in another project

That is why `search` and `export` default to `--scope all`. Pass the printed
`<project>/<module>/<file>.md` to `integrate`; a bare filename also works when it is unique across
the store, and `integrate` lists the candidates instead of guessing when it is not.

### Too many pending entries

Export and integrate the older ones:
```bash
python3 skills/kora-journal/scripts/kora_journal.py export --since 2026-01-01 --status pending
# After integrating, mark each as integrated (one entry per call — no globbing):
python3 skills/kora-journal/scripts/kora_journal.py integrate 2026-01-15_slug.md
```

### Entry file corrupted

Each entry is independent. A file with broken front matter is still listed — the date falls back to
the filename and the title to the file stem — and the other entries are unaffected. Delete and
recreate the corrupted entry.

---

## See Also

- [Kora Journal SKILL.md](../SKILL.md) — Main skill documentation
- [Entry Template](../assets/journal-entry-template.md) — Entry format template
