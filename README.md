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
uv tool install "git+https://github.com/tedin7/subtask@main"   # or: uv tool install /path/to/checkout
subtask install --client all          # prints the plan for every detected client
subtask install --client all --yes    # applies it: MCP server + skill
subtask doctor --client all           # checks app, config entry and a real handshake
```

Jev provides advisory workflow checks automatically, or only when requested:

```sh
subtask jev                 # policy, key availability and the current process's budget limits
subtask jev setup           # guided setup; key input is hidden
subtask jev auto            # automatic routing, feedback and final delivery advice
subtask jev manual          # explicit review submissions only
subtask jev off             # block new Jev submissions
```

New installations start in manual mode. The policy and private key are shared by
the configured clients. Enter keys through hidden `subtask jev setup` input or
explicit stdin, never chat, argv or tool arguments. In `auto`, `start_many`
appends routing advice, `reply` includes feedback advice, and final `wait` / `result`
include evidence consistency and risk triage. Read this advice together with the existing
baseline output. Routing and feedback preserve authorized work and worker
instructions. `manual` requires explicit submissions; `off` blocks new ones.

These extra checks share the existing cap and deduplicate identical content.
`skipped`, `blocked` and `unknown` are not passes; never retry an unknown send.
Distinguish raw `provider_choice` from filtered `assessment`: a failed verification
can veto `supported` deterministically, without being a Jev finding. Suggested
next actions grant no authority, replace no checks, and trigger no automatic model
fallback, merge, retry or release. See [Jev setup and limits](docs/delivery-review.md#configurazione)
and the [workflow study and its limits](docs/jev-workflow-20261026.md). These
instructions describe the upcoming release; they do not certify a live install.

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
| `start(task, kind, repo, cwd, files, readonly, engine, max_usd, effort, base, max_turns, policy, verify, isolation, max_retries, adversarial, context_refs, auto_repomap, tier, invariants, allowed_paths, symbol_scope, oracle_cmd)` | start one job (see below) |
| `start_many(tasks)` | start several; partial success is listed per item. Items with `id` and `depends_on` form a group (`grp-…`) run as a DAG without extra turns; `foreach=<parent id>` makes one copy per item of the parent's `.subtask/fanout.json` (at most 8), and dependents wait for every copy; `after="any"` starts when one parent succeeded |
| `integrate(ids, action="apply", auto_resolve=False)` | worktree jobs back: `diff`, `apply` (all or nothing, uncommitted), `discard`; `auto_resolve` starts a reconciliation job on conflict |
| `consensus(prompt, engines, verify_cmd, isolation="worktree")` | 2 to 4 engines solve it blind in their own worktrees, then one job compares their diffs as anonymous patches and writes one solution, checked by `verify_cmd` and the invariants; returns a group id |
| `bisect(good_ref, bad_ref, test_cmd, parallelism=4, auto_fix=False)` | first commit that makes `test_cmd` fail, testing up to `parallelism` (max 8) commits per round in temporary worktrees; returns the commit, author and diff; `auto_fix` starts a verified fix job |
| `map_reduce(glob_pattern, prompt_template, batch_size=5, concurrency=3, verify_cmd, isolation="worktree")` | one job per batch of files (`{{files}}` in the template), at most `concurrency` at a time; returns a group id |
| `wait(ids, timeout=300, mode="all", since=None, stall_timeout=None)` | block until they end (`any`: the first; `first_success`: the first done and verified, the others are stopped); returns early on a stalled job; progress notifications while waiting; `since` cursor for clients without progress |
| `steer(id, action, instruction=None)` | `hint` (queued, sent as a reply when the attempt ends), `abort`, `retry` (fresh job with the instruction) |
| `inspect(id, tail=80, grep=None)` | raw log lines, only when the summary is not enough |
| `status(id)` / `result(id, full, evidence)` | one line / summary or full text or JSON envelope |
| `reply(id, message)` / `stop(id)` / `cleanup(id)` | continue / stop the work / remove a clean worktree (branch kept) |
| `engines()` | capacity per engine with source, freshness and confidence, plus spend |

Options of `start`:

- `isolation="worktree"`: own worktree and branch (like `repo=`), plus `SUBTASK_PORT` (from 18100),
  `SUBTASK_ID` and a private `TMPDIR`. The result lists `files_modified` and `diff_stat`.
- `verify` + `max_retries`: a failed check resumes the job with the output; when every retry fails, a
  worktree goes back to its best attempt (scored from "N passed / N failed").
- `adversarial=True`: a Breaker job writes edge-case tests from the spec only; they are copied into the
  Builder's worktree and run with `verify`.
- `context_refs`: `file:PATH[:A-B]`, `git:diff[:REF]`, `subtask:ID:summary|diff`, resolved before start.
  `auto_repomap=True` adds tracked files with their top-level symbols (cached per HEAD).
- `invariants` (default `["no_test_deletion", "no_suppression"]`, `[]` turns them off): before `verify` counts,
  test functions may not decrease, no new skip/xfail markers, no new `# type: ignore`, `# noqa` or
  `# pragma: no cover`. A violation is a failed check with the reason, so a retry sees it.
- `allowed_paths`: files changed outside these paths are put back and the revert is committed.
- `symbol_scope=["path.py::name", ...]`: only those top-level functions or classes may change in those
  files. `integrate` then merges several jobs' edits to one Python file symbol by symbol; overlaps,
  import or constant changes, and symbols changed on the target meanwhile are a conflict, never a guess.
- `oracle_cmd`: after a passing `verify`, runs the command on the job's base and on its result at the same
  time and reports whether exit code and stdout match, plus `duration_ms_before`, `duration_ms_after`,
  `speedup_pct`.
- `tier`: `fast` (effort low), `deep` (effort high, kind hard), `free_only`. Past 75% of the monthly cap,
  `auto` and `balanced` go to free engines and the reply carries `budget_warning`.

Memory: when a check fails and a retry fixes it, subtask stores a short lesson for that repository in
`$SUBTASK_HOME/engrams/` (at most 50, least recently used dropped). Jobs on the same paths get the top three.
Groups have a blackboard file for shared notes; new notes show in `wait(since=...)`.

CLI only: `subtask board [--json]` (the view the OpenCode plugin renders), `subtask doctor`, `gc [--apply]`, `du`, `roi`, `routing explain|calibrate`,
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
