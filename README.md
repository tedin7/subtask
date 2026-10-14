# subtask

<!-- mcp-name: io.github.tedin7/subtask -->

A local delegation scheduler for coding agents. One MCP server (and a twin
CLI) lets Claude Code, Codex, Kimi Code or OpenCode start work in parallel,
wait for it with a single call, and read back a short summary with evidence.
It picks an engine that can take the job, books money and slots safely across
processes, and says honestly how isolated each run is.

```
start_many([{task: "Fix the flaky test in tests/test_sync.py", repo: "/src/app", verify: "pytest -q tests/test_sync.py"},
            {task: "List every caller of parse_date()", kind: "read", readonly: true, cwd: "/src/app"}])
  -> st-4f2a91 started · sonnet-api · branch subtask/st-4f2a91 · sandbox
     st-77c0de started · haiku-api · /src/app · readonly
wait(["st-4f2a91", "st-77c0de"])          # Claude Code shows: Subtask · 01:16 · 1/2 ended — st-4f2a91 running tests: pytest -q
  -> st-4f2a91 done · sonnet-api · 6m · 0.412$ · verified · ...
     ## Summary (≤10 lines: the answer, files, commits, tests, what is left)
result("st-4f2a91", evidence=True)        # JSON: base/head, changed files, observed commands, verify exit code, cost
```

## Why

- **Save the caller's context.** The worker's search, test runs and dead ends
  stay in its own session; the caller reads about ten lines.
- **Few round trips.** `start_many` + one `wait` replace a polling loop.
- **Use whatever has room.** `engine="auto"` filters engines that can enforce
  the requested isolation, are installed, fit the budget and have quota, then
  ranks them with an explicit utility (`subtask routing explain`).
- **Money is a primitive.** Paid engines reserve their budget before any work,
  under one lock with the slot; every reservation is closed exactly once.
- **Evidence, not claims.** `verify="..."` runs your check on the commit the
  worker left; `result(evidence=True)` separates what subtask observed from
  what the worker reported.

## Install

The distribution is `subtask-mcp` (the PyPI name `subtask` is an unrelated
project). Full guide, per client, with native commands: [docs/install.md](docs/install.md).

```sh
uv tool install "git+https://github.com/tedin7/subtask@v0.2.4"   # or: uv tool install /path/to/checkout
subtask install --client all          # prints the plan for every detected client
subtask install --client all --yes    # applies it: MCP server + skill
subtask doctor --client all           # checks app, config entry and a real handshake
```

Claude Code plugin: `claude plugin marketplace add tedin7/subtask` then
`claude plugin install subtask@subtask`. Codex: `codex plugin marketplace add tedin7/subtask`.
API engines read `ANTHROPIC_API_KEY` from `~/.config/subtask/env` (chmod 600).

## Engines

| engine | pays with | read-only | isolation it can enforce |
|---|---|---|---|
| `haiku-api`, `sonnet-api`, `opus-api` | API key, monthly cap | yes | sandbox (Claude Code OS sandbox) |
| `codex` | Codex login (quota from local session files) | yes | sandbox (`workspace-write`), isolated |
| `opencode-go` | opencode-go subscription | no | worktree, isolated |
| `kimi` | Kimi login | no | worktree, isolated |
| `dsh` | DeepSeek key, same cap (`subtask-mcp[dsh]`) | no | worktree |
| `opencode` | OpenCode default provider (manual only) | no | worktree, isolated |

Add an engine without touching the core: an `EngineAdapter` registered under the
`subtask.engines` entry point (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Isolation, honestly

- `worktree` (default): a git worktree and branch per writing job. It prevents
  collisions; it is **not** a host sandbox.
- `sandbox`: the engine's own sandbox. Same user: a guard rail.
- `isolated`: bubblewrap. Read-only system, writable worktree and git dir only,
  a private HOME with only the engine's own state. Needs `bwrap`.
- `untrusted`: sandbox or stronger, and no network for the worker's commands.

A job that cannot get the policy it asked for is refused, never downgraded.
Output is redacted for common secret formats before it is saved or shown;
redaction is a second layer, not a guarantee. See [SECURITY.md](SECURITY.md).

## Tools (MCP) and CLI

| tool | what it does |
|---|---|
| `start(task, kind, repo, cwd, files, readonly, engine, max_usd, effort, base, max_turns, policy, verify)` | start one job |
| `start_many(tasks)` | start several; partial success is listed per item |
| `wait(ids, timeout=300, any=False, since=None)` | block until they end; progress notifications while waiting; `since` cursor for clients without progress |
| `status(id)` / `result(id, full, evidence)` | one line / summary or full text or JSON envelope |
| `reply(id, message)` / `stop(id)` / `cleanup(id)` | continue / stop the work / remove a clean worktree (branch kept) |
| `engines()` | capacity per engine with source, freshness and confidence, plus spend |
| `step(title, path, how, decisions)` | an independent cheap model narrates the step from the repo's real state |

CLI only: `subtask doctor`, `gc [--apply]`, `du`, `roi`, `routing explain|calibrate`,
`watch ID...`, `install`, `uninstall`, and `--model`, `--no-sandbox`, `--untrusted` on start.

## Progress in the chat

| client | during a `wait` |
|---|---|
| Claude Code 2.1 (TUI) | live line under the tool call, verified |
| OpenCode 2.0 | notifications received, not shown: use `wait(since=...)` |
| Kimi Code 2.1 | client does not request progress: `wait(since=...)` |
| Codex 0.162 | not yet verified (account at its usage limit) |

Details and evidence: [docs/progress-matrix.md](docs/progress-matrix.md).

## Settings

| variable | default |
|---|---|
| `SUBTASK_HOME` | `~/.local/share/subtask` (jobs, worktrees, ledger, telemetry) |
| `SUBTASK_ENV_FILE` | `~/.config/subtask/env` |
| `SUBTASK_MONTHLY_CAP_USD` / `SUBTASK_DEFAULT_MAX_USD` / `SUBTASK_MAX_JOB_USD` | `180` / `2` / `20` |
| `SUBTASK_MAX_RUNNING` | `4` (global across all processes) |
| `SUBTASK_ROUTING` | `~/.config/subtask/routing.json` (weights, `"mode": "legacy"`) |
| `SUBTASK_JOB_RETENTION_DAYS` / `SUBTASK_TELEMETRY_DAYS` | `30` / `90` |
| `SUBTASK_PROGRESS_MIN_S` / `SUBTASK_PROGRESS_HEARTBEAT_S` | `2` / `10` |

Costs of API engines come from the SDK's estimate; a run whose real cost is
unknown is charged its whole reservation. Telemetry stays on this machine.

## Platforms

Linux: supported. macOS: degraded (no `/proc` start times for liveness, no
bwrap: `isolated` refused). Windows: not supported (`fcntl`).

## Documentation

[Architecture](docs/architecture.md) · [Requirements matrix](docs/requirements.md) ·
[Benefits and measurements](docs/benefits.md) · [Benchmark protocol](docs/benchmark.md) ·
[Install](docs/install.md) · [Releasing](RELEASING.md) · [Contributing](CONTRIBUTING.md)

## License

MIT
