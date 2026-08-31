# Repo scripts

## `version.py` — single source of truth for plugin versions

This repository ships more than one plugin: `kora-v1` (Kora 1.x) and `kora-v2` (Kora 2.x). Each
has its own version, and each version appears in six places — three manifests, two meta-skills,
and the plugin's own entry in the shared marketplace file. Never edit them by hand; they drift.

The canonical value for a plugin lives in `plugins/<plugin>/.claude-plugin/plugin.json`. This
script propagates it and verifies it.

```bash
python scripts/version.py                       # print every plugin version
python scripts/version.py get kora-v2           # print one plugin's version
python scripts/version.py check                 # exit 1 if anything drifts (all plugins)
python scripts/version.py check kora-v2         # check a single plugin
python scripts/version.py set 0.3.0 kora-v2     # write 0.3.0 across kora-v2 only
python scripts/version.py bump patch kora-v2    # 0.2.0 -> 0.2.1 (also: minor, major)
python scripts/version.py set 0.3.0 marketplace # the marketplace manifest's own version
```

Add `--dry-run` to `set` / `bump` to see the edits without writing them.

**`set` and `bump` require an explicit plugin name.** With several plugins in one repository a
default would eventually bump the wrong one, so there is none.

Synced per plugin: `plugins/<plugin>/.claude-plugin/plugin.json`,
`plugins/<plugin>/.codex-plugin/plugin.json`, `plugins/<plugin>/skill.json`,
`plugins/<plugin>/SKILL.md`, `plugins/<plugin>/skills/kora-starter/SKILL.md`, and that plugin's
entry inside `.claude-plugin/marketplace.json`.

The marketplace file is spliced per entry using the standard-library JSON scanner, so bumping one
plugin never rewrites another plugin's entry or the marketplace's own top-level `version`. The
top-level value is addressed separately, as the pseudo-plugin `marketplace`.

`.agents/plugins/marketplace.json` carries no version field, so it is intentionally not rewritten —
but `check` does verify that it lists every plugin, since a plugin missing there is invisible to
Codex.

Pure standard library — no `jq`, no PyYAML.
