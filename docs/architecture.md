# subtask architecture (v0.2)

Status: adopted 2026-10-14. Baseline: `3f731f6`. Every requirement in
[requirements.md](requirements.md) points to a section here.

subtask is a local delegation scheduler for coding agents. The parent agent
spends little context, any of several engines can run the work, money and
slots are booked safely, isolation is stated honestly, and the result comes
back short and with evidence. It is not a GUI, a Kanban board or a project
manager (report: "Da evitare"), and it has no database or remote worker until
data shows a need (report: "Solo quando richiesto dai dati").

## 1. Layers

```
MCP client / CLI                      fastserver.py (dual-era JSON-RPC), server.py (FastMCP reference), cli.py
  -> application service              core.py: start, start_many, wait, result, reply, stop, cleanup, step
     -> policy and routing            policy.py (SecurityPolicy), router.py (filter, then utility), capacity.py
     -> admission, slots, budget      admission.py (global slot lock + active index), ledger.py (reservations)
     -> workspace and runner          workspace.py (git worktree), runners.py (direct | bwrap isolated)
     -> engine adapter                engines/ (EngineAdapter protocol, built-ins, entry-point plugins)
     -> evidence and result           evidence.py (ResultEnvelope), redact.py
Cross-cutting: jobs.py (state, recovery, process identity), events.py (ProgressEvent log),
               progress.py (MCP progress reporter), telemetry.py + roi.py, doctor.py, gc.py,
               install/ (client integrations), narrate.py (step narrator)
```

Rules: `core` never builds provider argv; adapters never know about MCP or
chat rendering; servers never touch job files directly. Imports stay lazy so
`initialize`/`server/discover` answer before `core` is loaded.

## 2. Data contracts (all persisted formats carry `"v"`)

| Contract | Where | Fields (v1) |
|---|---|---|
| JobSpec | `jobs/<id>/spec.json` | v, task, engine, kind, repo, base, branch, git_dir, cwd, policy, model, max_usd, max_turns, effort, verify, created |
| JobState | `jobs/<id>/status.json` | v, state (queued, running, done, failed, stopped, unknown), attempt, proc {pid, start_ticks, boot_id}, liveness (alive, dead, unknown), cost_usd, session_id, detail, error, updated |
| Attempt | status.attempts[] | n, kind (start, reply), reservation, started, ended, outcome, cost_observed, cost_charged |
| Reservation | `ledger/YYYY-MM.jsonl` | v=2, ts, job, rid (`<job>#<n>`), op (reserve, settle, release), usd, month (month of the reserve) |
| ActiveEntry | `active.json` | job, phase (admitting, running), owner {pid, start_ticks}, since |
| EngineCapabilities | adapter | readonly, fs_sandbox, network_policy, cost_observable, resume, structured_events, policies[] |
| CapacitySnapshot | `capacity.json` | per engine: value, unit (pct_free, usd_left), source, ts, freshness_s, confidence (high, medium, low, none), assumption |
| SecurityPolicy | spec.policy | level (readonly, worktree, sandbox, isolated), network (provider-only, inherit), untrusted |
| ProgressEvent | `jobs/<id>/events.jsonl` | v, job, attempt, seq, ts, phase, state, msg (redacted), source (lifecycle, adapter) |
| ResultEnvelope | `result(evidence=True)` | v, id, engine, model, state, verification, base, head, branch, changed_files, commits, tests[], cost {observed, reserved, charged, unit}, summary, evidence_level per item |

Legacy files (no `v`) are read as v0 and upgraded on the next write.
`ledger.jsonl` is split once into monthly files; the original is kept as
`ledger.jsonl.v0.bak` (rollback: move it back, monthly files are ignored by v0).

## 3. Start flow and invariants

```
select -> admit(slot + money) -> prepare(worktree) -> spawn -> confirm -> run -> finalize -> settle
```

1. **select** (no lock): router filters engines that satisfy the policy, are
   installed, have budget; ranks the rest; returns a ranked list and the reason.
2. **admit** (lock order everywhere: job lock, then `admission.lock`, then the
   ledger lock; never the reverse; `reply` holds the job lock while admitting):
   rebuild the active index from entries whose owner or worker is not alive,
   count, refuse at `MAX_RUNNING`; for paid engines write `reserve` for
   `rid = job#1`. Release both locks. No git, network or spawn under the lock.
3. **prepare**: worktree and spec. On failure: `release(rid)` + drop the slot.
4. **spawn**: detached worker; record pid + `/proc/<pid>/stat` start ticks +
   boot id. On OSError: release + drop slot + state failed.
5. **run**: the worker never reserves on a first attempt (the parent did);
   on `reply` the reservation is made under the job lock by `core.reply`.
6. **finalize**: worker writes result, status and one `settle(rid, cost)`.
7. **recovery**: `status()` of a job whose worker is provably dead settles the
   open rid at its prudential amount (`max_usd` if the engine may have
   spent); unknown liveness keeps state and reservation and shows
   `liveness: unknown`.

Invariants (tested by multiprocess tests):
- I1 active jobs (admitting + running) never exceed `MAX_RUNNING` across processes.
- I2 for every month, reserve + settled <= cap at every instant.
- I3 every rid has at most one closing row (settle or release); a second close is a no-op.
- I4 a worker is declared dead only with proof (process gone, or pid alive with different start ticks).
- I5 a job dir is never left without spec + status once `start` returned an id.

Not promised: exactly-once for provider side effects. A crash between spawn
and pid record leaves an `admitting` entry whose owner is dead; recovery marks
it failed and charges the prudential amount only if the worker wrote
`running`.

`start_many` admits each item independently; the reply lists started ids and
refused items. `wait` is read-only: cancelling a wait never stops jobs;
`stop` stops the work.

## 4. Engines

`engines/base.py` defines `EngineAdapter` (name, capabilities(), probe(),
build(job) -> RunPlan, parse_event(line) -> ProgressEvent | None,
finish(output) -> RunOutcome, resume support). Built-ins: claude API
(sonnet/haiku/opus), codex, kimi, opencode, opencode-go, dsh. Third-party
adapters register through the `subtask.engines` entry point; a test adapter
proves the core does not change to add one.

`probe()` separates installed, version, flags (from `--help`), auth (only from
local evidence, never a paid call), sandbox available. Results cache with a
timestamp; stale data is labelled.

## 5. Security

Policy levels, strongest last: `readonly` < `worktree` < `sandbox` < `isolated`.
The default demand is `worktree`; each engine then runs at its strongest
native level at or above it (Codex and the Claude engines run `sandbox`).
A job asks for a minimum level; the router only keeps engines whose
capabilities can enforce it, and the runner fails closed when enforcement is
missing. No automatic downgrade. `untrusted=True` implies `sandbox` or
higher, minimal env, and no network for worker commands where the engine can
enforce it.

- `worktree`: git isolation only. Not a host sandbox (stated in output).
- `sandbox`: engine-native sandbox (Claude SDK OS sandbox; Codex
  `-s workspace-write`). Same user: not a strong boundary.
- `isolated`: `bwrap` runner. Read-only system, writable worktree and git dir
  only, tmpfs HOME with only the engine's credential file bound read-only,
  new pid/ipc/uts namespaces, no access to the rest of HOME. Model traffic
  needs network; worker-command network is limited by the engine sandbox
  when present. Documented as stronger than same-user deny rules, weaker than
  a separate user or VM.

Environment: allow-list (PATH, HOME, LANG, TERM, TMPDIR, engine-specific).
Redaction (`redact.py`) runs before anything is persisted or returned:
provider key formats, private keys, JWT, `key=value` secrets, plus the exact
values of secrets subtask itself loaded.

## 6. Routing

Filter (hard constraints): policy, installed, readonly support, budget,
capacity below 10 % when known. Rank by utility with explicit units:

```
u = w_s * P(success | engine, kind)          # probability, prior or learned
  - w_c * expected_cost_usd / job_budget_usd # 0..1
  - w_l * expected_minutes / 60              # hours
  - w_q * scarcity                           # 1 - free/100, or prior when unknown
```

Weights live in `routing.json`; defaults reproduce the old heuristic order
(baseline kept as `router.legacy_pick`). Learned P(success) is used only with
>= `min_samples` verified outcomes per cell; otherwise the prior is used and
named as such. `subtask routing explain|calibrate` shows terms and sample counts.
Fallback: at most one other engine, only before the worker started.

## 7. Results, telemetry, ROI

Default reply stays the short summary. `result(id, evidence=True)` returns the
envelope built from observed data: git base/head/changed files/commits
(`runner_observed`), commands with exit codes from engine events
(`runner_observed`), claims from the summary (`worker_reported`), and the
optional `verify` command run by subtask on the recorded head
(`independently_verified`).

Telemetry rows (`telemetry/YYYY-MM.jsonl`, v1, redacted, retention 90 days):
durations (queue, run), tokens if the engine reports them, cost observed /
reserved / charged, summary bytes and full result bytes, round trips per job,
replies, outcome, verification, routing terms. `subtask roi` reports separate
ratios (context saved estimate, USD per accepted task, latency, success) with
assumptions and a sensitivity line; no composite of tokens over dollars plus seconds.

## 8. Progress in the chat

Source: `events.jsonl` written by worker lifecycle and adapter parsers.
`progress.py` runs one reporter per in-flight tool call that carries
`_meta.progressToken`: it tails the events of the involved jobs and emits
`notifications/progress` with a monotone counter (`progress` = significant
updates so far, `total` = number of jobs for wait), and a message like
`Subtask · 01:16 · 2/3 done · st-b2 running tests`. Throttle: significant
updates at most every 2 s, heartbeat every 10 s; bounded queue; a slow client
never blocks a worker. Without a token: no notifications; `wait` returns early
with `since` cursor deltas (fallback). Cancellation (`notifications/cancelled`)
ends the wait only. stdout carries JSON-RPC only, one writer lock.

## 9. Distribution

The PyPI name `subtask` belongs to another project, so the distribution is
`subtask-mcp` (import package stays `subtask`), installable today from a git
tag with `uv tool install git+https://github.com/tedin7/subtask@v0.2.0`.
`subtask install --client claude|codex|kimi|opencode|all` uses each client's
native command when it exists, else a targeted config merge with backup,
atomic write, ownership marker and rollback. A Claude Code marketplace lives in
the repo (`.claude-plugin/marketplace.json`).

## 10. Decisions

| Decision | Alternatives | Why |
|---|---|---|
| Files + fcntl, active index | SQLite WAL | 4 jobs default; report says DB only with proven need |
| Parent reserves money at admit | worker reserves | routing and reservation see the same ledger state; no wasted worktree |
| Monthly ledger files | single file | O(month) reads, natural retention |
| bwrap isolated runner | container, separate user | present on target hosts, no daemon, no root |
| Dual-era MCP server | modern only | Claude Code, Codex and others still speak 2025-11-25 |
| dist name `subtask-mcp` | `subtask` | PyPI name taken by an unrelated project |
