#!/usr/bin/env python3
"""Single source of truth for plugin versions in this marketplace.

The repository ships more than one plugin (`kora-v1` for Kora 1.x, `kora-v2` for Kora 2.x).
Each has its own canonical version, held in its Claude Code plugin manifest
`plugins/<plugin>/.claude-plugin/plugin.json`. Every other manifest and meta-skill of that
plugin must match it, including the plugin's own entry inside the shared marketplace file.

Mutating commands REQUIRE an explicit plugin name. That is deliberate: with several plugins
in one repository, a `bump` that defaults to something would eventually bump the wrong one.

Usage:
    python scripts/version.py                       # print every plugin version
    python scripts/version.py get                   # same
    python scripts/version.py get kora-v2           # print one plugin's version
    python scripts/version.py check                 # exit 1 if anything drifts (all plugins)
    python scripts/version.py check kora-v2         # check a single plugin
    python scripts/version.py set 0.3.0 kora-v2     # write 0.3.0 across kora-v2 only
    python scripts/version.py bump patch kora-v2    # 0.2.0 -> 0.2.1 (also: minor, major)
    python scripts/version.py set 0.3.0 marketplace # the marketplace manifest's own version

    Add --dry-run to `set` / `bump` to print the edits without writing them.

No third-party dependencies (no jq, no PyYAML) — targeted edits that preserve each file's
existing formatting and key order. The shared marketplace file is spliced per plugin entry
using the stdlib JSON scanner, so one plugin's bump never rewrites another's entry.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MARKETPLACE = ".claude-plugin/marketplace.json"

# Per-plugin file sets. The canonical version of a plugin lives in its Claude Code manifest;
# every other entry here must match it. Paths are relative to the repository root.
PLUGINS: dict[str, dict[str, list[str]]] = {
    "kora-v1": {
        "canonical": ["plugins/kora-v1/.claude-plugin/plugin.json"],
        "json": [
            "plugins/kora-v1/.claude-plugin/plugin.json",
            "plugins/kora-v1/.codex-plugin/plugin.json",
            "plugins/kora-v1/skill.json",
        ],
        "md": [
            "plugins/kora-v1/SKILL.md",
            "plugins/kora-v1/skills/kora-starter/SKILL.md",
        ],
    },
    "kora-v2": {
        "canonical": ["plugins/kora-v2/.claude-plugin/plugin.json"],
        "json": [
            "plugins/kora-v2/.claude-plugin/plugin.json",
            "plugins/kora-v2/.codex-plugin/plugin.json",
            "plugins/kora-v2/skill.json",
        ],
        "md": [
            "plugins/kora-v2/SKILL.md",
            "plugins/kora-v2/skills/kora-starter/SKILL.md",
        ],
    },
}

SEMVER = r"\d+\.\d+\.\d+"
JSON_VERSION = re.compile(r'("version"\s*:\s*")' + SEMVER + r'(")')
MD_VERSION = re.compile(r'^(\s*version:\s*")' + SEMVER + r'(")', re.MULTILINE)

# `.agents/plugins/marketplace.json` carries no version field, so it is intentionally
# not touched — but it must list the same plugins, which `check` verifies.
AGENTS_MARKETPLACE = ".agents/plugins/marketplace.json"


# --------------------------------------------------------------------------------------
# marketplace.json splicing
# --------------------------------------------------------------------------------------

def _marketplace_text() -> str:
    return (REPO / MARKETPLACE).read_text(encoding="utf-8")


def _plugin_entry_span(text: str, plugin: str) -> tuple[int, int]:
    """Character span of one object inside the top-level `plugins` array.

    The stdlib scanner gives exact object boundaries, so a bump of one plugin cannot
    disturb a sibling entry or the marketplace's own fields.
    """
    key = text.index('"plugins"')
    start = text.index("[", key)
    decoder = json.JSONDecoder()
    idx = start + 1
    while True:
        while idx < len(text) and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= len(text) or text[idx] == "]":
            raise KeyError(f"{plugin} is not listed in {MARKETPLACE}")
        obj, end = decoder.raw_decode(text, idx)
        if obj.get("name") == plugin:
            return idx, end
        idx = end


def marketplace_versions(plugin: str) -> list[str]:
    text = _marketplace_text()
    entry = text[slice(*_plugin_entry_span(text, plugin))]
    return [re.search(SEMVER, m.group(0)).group(0) for m in JSON_VERSION.finditer(entry)]


def marketplace_own_version() -> str:
    """The marketplace manifest's own top-level version, outside the plugins array."""
    data = json.loads(_marketplace_text())
    return data["version"]


def write_marketplace_entry(plugin: str, new: str, dry_run: bool) -> int:
    text = _marketplace_text()
    start, end = _plugin_entry_span(text, plugin)
    entry = text[start:end]
    updated_entry, n = JSON_VERSION.subn(rf"\g<1>{new}\g<2>", entry)
    if n and updated_entry != entry and not dry_run:
        (REPO / MARKETPLACE).write_text(
            text[:start] + updated_entry + text[end:], encoding="utf-8", newline="\n"
        )
    return n


def write_marketplace_own(new: str, dry_run: bool) -> int:
    """Rewrite only the top-level `version`, leaving every plugin entry untouched."""
    text = _marketplace_text()
    key = text.index('"plugins"')
    head, tail = text[:key], text[key:]
    updated_head, n = JSON_VERSION.subn(rf"\g<1>{new}\g<2>", head, count=1)
    if n and updated_head != head and not dry_run:
        (REPO / MARKETPLACE).write_text(updated_head + tail, encoding="utf-8", newline="\n")
    return n


# --------------------------------------------------------------------------------------
# plugin file sets
# --------------------------------------------------------------------------------------

def resolve(plugin: str) -> dict[str, list[str]]:
    if plugin not in PLUGINS:
        sys.exit(f"error: unknown plugin '{plugin}'; known: {', '.join(PLUGINS)}")
    return PLUGINS[plugin]


def read_canonical(plugin: str) -> str:
    path = REPO / resolve(plugin)["canonical"][0]
    m = JSON_VERSION.search(path.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"error: no version found in {path.relative_to(REPO)}")
    return re.search(SEMVER, m.group(0)).group(0)


def file_targets(plugin: str) -> list[tuple[Path, re.Pattern]]:
    spec = resolve(plugin)
    return [(REPO / p, JSON_VERSION) for p in spec["json"]] + \
           [(REPO / p, MD_VERSION) for p in spec["md"]]


def occurrences(path: Path, pattern: re.Pattern) -> list[str]:
    if not path.exists():
        return []
    return [re.search(SEMVER, m.group(0)).group(0)
            for m in pattern.finditer(path.read_text(encoding="utf-8"))]


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------

def cmd_get(plugin: str | None) -> None:
    if plugin:
        print(read_canonical(plugin))
        return
    print(f"{'marketplace':<14} {marketplace_own_version()}")
    for name in PLUGINS:
        print(f"{name:<14} {read_canonical(name)}")


def check_plugin(plugin: str) -> list[str]:
    want = read_canonical(plugin)
    drift = []
    for path, pattern in file_targets(plugin):
        if not path.exists():
            drift.append(f"  {path.relative_to(REPO)}: file missing")
            continue
        found = occurrences(path, pattern)
        if not found:
            drift.append(f"  {path.relative_to(REPO)}: no version field found")
        drift += [f"  {path.relative_to(REPO)}: {v} (expected {want})"
                  for v in found if v != want]
    try:
        entry_versions = marketplace_versions(plugin)
    except KeyError as exc:
        drift.append(f"  {MARKETPLACE}: {exc}")
    else:
        if not entry_versions:
            drift.append(f"  {MARKETPLACE}: {plugin} entry has no version field")
        drift += [f"  {MARKETPLACE} [{plugin}]: {v} (expected {want})"
                  for v in entry_versions if v != want]
    return drift


def check_agents_marketplace() -> list[str]:
    """Codex's manifest has no versions, but it must offer the same plugin set."""
    path = REPO / AGENTS_MARKETPLACE
    if not path.exists():
        return [f"  {AGENTS_MARKETPLACE}: file missing"]
    listed = {p.get("name") for p in json.loads(path.read_text(encoding="utf-8"))["plugins"]}
    return [f"  {AGENTS_MARKETPLACE}: {name} not listed" for name in PLUGINS if name not in listed]


def cmd_check(plugin: str | None) -> None:
    names = [plugin] if plugin else list(PLUGINS)
    failed = False
    for name in names:
        drift = check_plugin(name)
        if drift:
            failed = True
            print(f"{name}: version drift from canonical {read_canonical(name)}:")
            print("\n".join(drift))
        else:
            print(f"ok: {name} all version fields at {read_canonical(name)}")
    if plugin is None:
        drift = check_agents_marketplace()
        if drift:
            failed = True
            print(f"{AGENTS_MARKETPLACE}: plugin set mismatch:")
            print("\n".join(drift))
        else:
            print(f"ok: {AGENTS_MARKETPLACE} lists every plugin")
    if failed:
        sys.exit(1)


def write_version(plugin: str, new: str, dry_run: bool) -> None:
    if not re.fullmatch(SEMVER, new):
        sys.exit(f"error: '{new}' is not a valid x.y.z version")
    prefix = "[dry-run] " if dry_run else ""

    if plugin == "marketplace":
        n = write_marketplace_own(new, dry_run)
        print(f"{prefix}  {MARKETPLACE} [marketplace]: {n} field(s) -> {new}")
        return

    changed = 0
    for path, pattern in file_targets(plugin):
        if not path.exists():
            print(f"{prefix}  {path.relative_to(REPO)}: MISSING, skipped")
            continue
        text = path.read_text(encoding="utf-8")
        updated, n = pattern.subn(rf"\g<1>{new}\g<2>", text)
        if n and updated != text:
            if not dry_run:
                path.write_text(updated, encoding="utf-8", newline="\n")
            changed += 1
        if n:
            print(f"{prefix}  {path.relative_to(REPO)}: {n} field(s) -> {new}")
    n = write_marketplace_entry(plugin, new, dry_run)
    if n:
        changed += 1
        print(f"{prefix}  {MARKETPLACE} [{plugin}]: {n} field(s) -> {new}")
    print(f"{prefix}updated {changed} file(s) for {plugin} to {new}")


def cmd_bump(part: str, plugin: str, dry_run: bool) -> None:
    if part not in ("major", "minor", "patch"):
        sys.exit("error: bump expects one of: major, minor, patch")
    if plugin == "marketplace":
        current = marketplace_own_version()
    else:
        current = read_canonical(plugin)
    major, minor, patch = (int(x) for x in current.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    write_version(plugin, f"{major}.{minor}.{patch}", dry_run)


def main(argv: list[str]) -> None:
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]

    if not argv or argv[0] == "get":
        cmd_get(argv[1] if len(argv) == 2 else None)
    elif argv[0] == "check":
        cmd_check(argv[1] if len(argv) == 2 else None)
    elif argv[0] == "set" and len(argv) == 3:
        write_version(argv[2], argv[1], dry_run)
    elif argv[0] == "bump" and len(argv) == 3:
        cmd_bump(argv[1], argv[2], dry_run)
    elif argv[0] in ("set", "bump"):
        sys.exit(f"error: '{argv[0]}' requires an explicit plugin name "
                 f"({', '.join(PLUGINS)}, or marketplace) so the wrong plugin is never touched")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
