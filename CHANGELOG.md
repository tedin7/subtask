# Changelog

## 0.2.6 - 2026-10-14

Same content as 0.2.5, which never reached PyPI: its release stopped on a timing test too strict for CI runners (now relative).

## 0.2.5 - 2026-10-14 (not published)

### Fixed
- A paid job started by an older server (no reservation) is now booked by the worker, so its spend counts against the cap.

## 0.2.4 - 2026-10-14

### Changed
- Claude Code: `subtask install --client claude` installs the plugin (server, skill, timing hook) and removes the older
  `claude mcp add` entry and skill copy, so the server is registered once. `--no-plugin` keeps the old route.
- Plugin launcher: the installed `subtask-mcp` when present, otherwise `uvx --from subtask-mcp==X` from PyPI.
- Releases publish themselves from a tag: PyPI via Trusted Publishing, GitHub release, MCP Registry via OIDC.

### Fixed
- OpenCode 2 plugin API (default `{id, setup}`), verified loaded in 2.0.28.

## 0.2.3 - 2026-10-14

### Added
- Published on PyPI as `subtask-mcp`; `mcp-name` for the MCP Registry.

## 0.2.2 - 2026-10-14

### Fixed
- macOS: process start times read with `ps` (liveness was always unknown).
- `policy=isolated` checks that bubblewrap can really create namespaces; refused otherwise.
- OpenCode: `opencode.jsonc` is read; a config with comments is never rewritten.
- CI collects only `tests/`.

## 0.2.1 - 2026-10-14

### Fixed
- `subtask install` failed when the client's skill folder was a symlink (e.g. a broken link to an old checkout); the link is now replaced by a folder, its target untouched.

## 0.2.0 - 2026-10-14

### Added
- Engine adapters (one module per engine), probing, plugin engines via the `subtask.engines` entry point; `opencode-go` engine.
- Explainable router: admissibility filters, then an explicit utility; `routing explain|calibrate`; legacy mode kept.
- Capacity with unit, source, freshness and confidence; quota exhaustion remembered until reset.
- Evidence envelope (`result(evidence=True)`) and `verify=` checks run by subtask itself.
- Progress notifications during `wait`, `stop`, `start_many`; `wait(since=cursor)` fallback; cancellation.
- Local telemetry, `roi`, `gc`, `du`, `doctor`, `watch`; demo engine (`SUBTASK_DEMO=1`).
- `subtask install|uninstall|doctor --client claude|codex|kimi|opencode|all`; Claude and Codex plugins and marketplaces.
- Benchmark corpus (24 validated tasks) and harness; first live mini run.

### Changed
- MCP: dual-era server (2026-07-28 stateless with discovery and caching hints, plus initialize up to 2025-11-25).
- Global admission across processes; money reserved together with the slot before any work; ledger v2 in monthly files.
- Distribution renamed `subtask-mcp`; one version for package, CLI and MCP.
- Server instructions teach one `start_many` then one `wait`.

### Fixed
- `step.title` missing from the published schema.
- Kimi 2.1 jobs failed (`-p` with `--auto`).
- MAX_RUNNING could be exceeded by concurrent callers; 15 defects found by an independent review (slots, money, stop, recovery).

### Security
- Codex writes in its `workspace-write` sandbox by default (no more bypass unless `policy=worktree`).
- `policy=isolated` (bubblewrap) and `untrusted`; minimal worker environment; general secret redaction.

## 0.1.0

- Initial release.
