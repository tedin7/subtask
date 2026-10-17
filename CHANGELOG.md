# Changelog

## Unreleased

### Added
- `wait(mode="all"|"any"|"first_success")`: `first_success` returns the first job that is done and passed
  `verify`, and stops the others. `any=True` still works.
- Stall watchdog: a running job with no event for `SUBTASK_STALL_S` (600 s) or `stall_timeout`, or with the
  same error three times, is shown `STALLED` and ends `wait`. Nothing is killed.
- `steer(id, hint|abort|retry, instruction)` and `inspect(id, tail, grep)` tools.

## 0.2.11 - 2026-10-14

### Fixed
- Claude Code folds MCP results, so a `step` announcement the agent did not copy was never seen. The plugin's
  PostToolUse hook now shows it in the chat (verified live), still without sending anything to the agent.

## 0.2.10 - 2026-10-14

### Fixed
- Automatic step announcements said the user's request was missing when it sat before 400 KB of tool output
  (seen live); the whole transcript is now streamed line by line (83 ms on a very large session).

## 0.2.9 - 2026-10-14

### Added
- Automatic step announcements: `subtask narrate-hook`, wired as a PreToolUse hook in the Claude Code
  plugin. Before a file change or a commit it reads the user's request, the agent's recent actions and
  the exact change from the session, has Haiku explain them, and shows the text only to the user
  (verified live: the agent cannot see it, so it costs the agent no turn and no token). At most once per
  20 minutes per repository (`SUBTASK_STEP_EVERY_MIN`); never blocks a change.
- Skill and server instructions carry the measured rule: delegate long work to cheap engines.

## 0.2.8 - 2026-10-14

### Fixed
- A long `wait` counted one round trip per second of polling; now one tool call counts once.

### Added
- Live adapter suite `pytest -m live` (one tiny real job per engine, capped at 0.20 USD); the manual
  `contract` workflow runs it on the API engine with the `ANTHROPIC_API_KEY` secret.
- Portable Codex plugin manifest (root `plugin.json` + `mcp.json`), `.codex-plugin/` kept as fallback.
- Live benchmark: rerun on 0.2.7 and three hard tasks (docs/benchmark.md).

## 0.2.7 - 2026-10-14

### Fixed
- Jobs failed with `UnicodeDecodeError` when the progress log's 4096-byte tail started inside a multi-byte character
  (accents, `…`). The tail is read as bytes, the cut line is skipped, and a progress write can no longer fail a job.

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
