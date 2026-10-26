---
name: subtask
description: Delegate work to background sub-agents through the subtask MCP tools (or the `subtask` CLI). Use when there are 2+ independent pieces to run in parallel, long or noisy work whose details you do not need, or complex code that needs an independent reviewer. Not for small edits.
---

# subtask: delegate cheaply

Every token you spend on delegation must be cheaper than doing the work
yourself. Follow these rules.

## Delegate or not

Delegate when one of these is true:
- 2 or more independent pieces can run at the same time;
- the work is long or noisy and you only need the outcome (broad search,
  audit, refactor over many files, test-and-fix loop);
- complex code needs a reviewer who did not write it (concurrency, saved data
  or migrations, security, money, logic shared by many callers).

Measured (docs/benchmark.md): on small tasks delegating cost 2-3x the money
and time for the same result; on long, many-step pieces given to cheaper
engines (haiku-api, opencode-go, kimi) it cost 38-57 % less with about half
the parent context, but took 2-8x longer. So: delegate long work to cheap
engines; do small things yourself.

Do NOT delegate when:
- the change is small (a few lines, one file) or you already know the fix;
- it needs what is in your context right now (a decision you just made with
  the user, a half-read stack trace): the brief would be longer than the work;
- the next step depends on the answer within a minute: just do it;
- the pieces touch the same lines: run them one after the other yourself.

## The cheap call pattern

1. One call to start everything: `start_many([...])`, or `start(...)` for one job.
2. One call to collect: `wait(ids)`. It blocks until the jobs end and returns
   each summary. Do not poll `status()` in a loop and never `sleep` before
   checking. For background delivery keep a wait open:
   `wait(ids, mode="any", timeout=60, since="")` returns as soon as one job
   ends. Drop the ids it reports as ended, pass back the returned `cursor=`,
   and call it again for the rest.
3. Only if a summary is not enough: `result(id, full=True)`; for checkable
   facts (commits, changed files, test exit codes, cost):
   `result(id, evidence=True)`.
4. To correct a finished job: `reply(id, "also handle X")`. It keeps the
   sub-agent's context, so the brief is not sent again.

## Writing the brief

- State the goal, the done condition and how to verify it, in a few lines.
- Pass paths in `files=[...]`. Never paste file contents: the sub-agent reads them.
- Set `kind`: `read` (search, summarize: cheapest), `code`, `review`, `hard`.
- Set `readonly=True` for questions and audits. Use `repo=` for changes: the job
  gets its own worktree and branch `subtask/<id>`, commits there, never pushes.
- Give `verify="pytest -q tests/test_x.py"` when there is a check: subtask runs
  it itself on the commit the worker left, so "tests pass" is a fact, not a claim.
- `policy`: `worktree` (default, git isolation only, NOT a host sandbox),
  `sandbox` (engine sandbox), `isolated` (bubblewrap: only the worktree is
  writable, the home is hidden), `untrusted` (for repositories you do not trust).
  A job that cannot get the policy is refused, never silently downgraded.
- Leave `engine="auto"`: it filters engines by policy, install, budget and
  quota, then ranks them; the start reply says why it picked one.

## While jobs run

- Clients that show MCP progress display a live line during `wait`
  (`Subtask · 01:16 · 2/3 ended — st-b2 running tests`). You do not need to
  repeat it.
- If your client does not show progress, use `wait(ids, mode="any", timeout=60,
  since="")` and pass back the returned `cursor=` next time: each reply lists
  only what changed. Drop ids already reported as ended; otherwise `mode="any"`
  returns immediately on the old result. Tell the user one short line when
  something significant changed (a job ended, failed, or is blocked); stay
  silent otherwise.
- Cancelling a `wait` does not stop the jobs; `stop(id)` does.

## Codex: delivery, not wake (0.164.0)

For ordinary local MCP servers, Codex 0.164.0 accepts `notifications/progress`,
`notifications/message`, resource updates and tool-list changes, but its client
handler only writes them to its log (`rmcp-client/src/logging_client_handler.rs`);
none of them starts a model turn. A progress line or log message cannot wake an idle
Codex session. Do not claim otherwise, and do not build a "bridge" that pretends
it can.

The supported path is a blocking wait inside the turn:

```
wait(ids, mode="any", timeout=60, since="")   # returns at the first end
# remove the ids now ended, keep cursor= from the reply, call again for the rest
```

Each call is a blocking server-side wait: it returns immediately when a job
ends, or after at most 60 s with `still running` and a `cursor=`. That is not
the same as waking an inactive session: ordinary Subtask notifications do not
resume an ended turn. Report each completion as it arrives, then continue waiting
for the remaining ids. Do not end the turn with your jobs still outstanding.
Never `sleep 600` and then check `status()`.

Install for Codex 0.164.0 (the default MCP tool timeout is 300 s; the installer
writes 660 s, which long non-cursor waits need):

```sh
codex mcp add subtask -- "$(uv tool dir --bin)/subtask-mcp"
# ~/.codex/config.toml
[mcp_servers.subtask]
tool_timeout_sec = 660
```

The plugin route (`codex plugin marketplace add ...` then `codex plugin add
subtask@subtask`) brings the same skill. Verify with `codex mcp list` and
`subtask doctor --client codex`.

## Reading Jev advice

Jev is advisory: it never accepts, merges or retries work, and it does not run
the tests. Launch work with `verify` and judge the runner's evidence, never the
worker's own claim, before reading any Jev advice. These workflow instructions
describe the upcoming release, not proof that an installed runtime is current.

- Automatic: with policy `auto`, workflow advice runs automatically at the
  relevant calls: routing in `start_many`, feedback in `reply`, and evidence
  consistency plus risk triage at final `wait` / `result`. Read the advice together with
  the baseline output before deciding the next step.
  These are separate advisory questions, not proof of the whole delivery.
  Repeated identical content is deduplicated; do not submit it manually again.
- `start_many` routing checks proposed dependencies. Disjoint writes can still
  share reads: a reviewer reading a file being changed may need a dependency.
  Read the proposed action, then check actual checkout, commit and read/write
  sets. Advice does not delay authorized work or rewrite worker instructions.
- `reply` feedback checks the next instruction. If deployment proof is missing,
  ask for that proof rather than declaring deployment complete. Advice is
  returned with the baseline reply output; it does not rewrite the message or
  prevent the authorized continuation.
- Final evidence consistency compares claims with receipts. For example, failed
  verification blocks `supported` even when the raw provider answer supports
  the claim. Risk triage can distinguish routine text work from a credential
  migration; it cannot waive the independent review required for complex code.
- Policy `manual` prepares advice only on explicit request; `off` blocks new
  submissions. Respect both policies: do not switch to `auto` for convenience.
  Additional Jev calls use the existing shared cap; they add no budget or
  automatic fallback to another model.
- Manual: `review(id, criteria=[...], original_request="...")` prepares a local
  packet from the request, report, committed diff and recorded checks;
  `submit=True` sends one paid request. Use it for criteria the automatic check
  did not cover, or for full scope. Pass the coordinator's original request to
  the manual call: the automatic check never receives it, and worker-visible
  metadata cannot authenticate it. Without it, `scope_assessment` stays
  `unproven`: not a pass, not a contradiction, full scope not proven.
- `supported` is advice on one criterion, never acceptance of the delivery.
  `contradicted` means the material points the other way; `unproven` means the
  material cannot establish it. A worker's own report does not prove either.
- `provider_choice` is the provider's raw answer; `assessment` is the value
  after the local deterministic wrapper. Any provider answer can become
  `unproven` when the verification is missing, failed or tied to another commit:
  that veto is a local limit, not a finding by the model. Do not present it as
  the model detecting a bug, and do not read a downgraded answer as a pass.
- Make `skipped`, `blocked` and `unknown` states explicit; none is a pass.
  A missing key, exhausted budget, provider error or `unknown` submission stops
  the check and leaves the worker delivery unchanged. Report the message and do
  not retry: an unknown send may already be charged, and the invoice stays
  unknown. Keys are entered only with `subtask jev setup` (hidden terminal
  input) or explicit stdin, never in chat, argv or a tool argument. Changing
  the policy (`auto`/`manual`/`off`) is the operator's decision, not a way to force a check.
- A suggested next action is advisory: it grants no permission, runs no
  recovery and substitutes for no verification. Do not start a model fallback
  automatically. Routing and feedback never authorize extra scope.
- Records are mutable by the same OS user: this is advice, not attestation.
  Read `docs/delivery-review.md` for the limits.
- The delivery details also show the advisory filter route. A normal review
  remains necessary for unknown/invalid output, any unproven required criterion,
  a local veto, or missing/unproven full scope. It is not started automatically.
  A decisive Jev route does not waive independent checks or grant acceptance.
- Do not infer full-delivery coverage from a successful single-criterion check.
  Test-plan and recovery classifiers are experimental; use explicit criteria,
  compare them with the normal model, and do not execute their proposed actions.
  See `docs/jev-cascade-20261025.md` and `docs/jev-workflow-20261026.md` for
  the measured comparisons and their limits. The twelve workflow cases do not
  establish general accuracy, savings or production availability.

## After the job

- A summary is what the worker reports. Trust a `verified` status or the
  evidence envelope over a sentence like "all tests pass".
- Review the branch diff yourself before merging: `git diff <base>..subtask/<id>`.
- Merge the exact commits you reviewed, then `cleanup(id)`.
- If something failed, decide: `reply()` once with a precise fix, or take it
  over. Do not loop.

## Recipes (0.4)

Writes in parallel, checked, no cheating (invariants are on by default):
```
start_many([
  {"task": "Speed up wait()", "repo": "/src/app", "verify": "pytest -q tests/test_wait.py",
   "symbol_scope": ["app/server.py::wait"], "allowed_paths": ["app/server.py", "tests"]},
  {"task": "Add a timeout to steer()", "repo": "/src/app", "verify": "pytest -q tests/test_steer.py",
   "symbol_scope": ["app/server.py::steer"]}
])
wait([...]); integrate([...], action="diff"); integrate([...], action="apply")
```
Two jobs on one file merge by symbol when their `symbol_scope` do not overlap; anything
else is reported as a conflict, nothing is guessed.

Context without pasting: `"context_refs": ["file:app/db.py:40-120", "git:diff", "subtask:st-1a2b:summary"],
"auto_repomap": true`.

Same behaviour, faster: `start(task, repo=..., verify="pytest -q", oracle_cmd="python bench.py")`; the result
says `oracle ...: same output · +42% (180ms → 104ms)` or `OUTPUT DIFFERS`.

Discover, then fan out (the parent writes a JSON list to `.subtask/fanout.json`, at most 8):
```
start_many([
  {"id": "find", "task": "List slow queries; write them as a JSON list to .subtask/fanout.json", "repo": "/src/app"},
  {"id": "fix", "depends_on": ["find"], "foreach": "find", "task": "Fix {{item}}", "repo": "/src/app",
   "verify": "pytest -q"},
  {"id": "check", "depends_on": ["fix"], "task": "Run the full suite and report", "readonly": true, "cwd": "/src/app"}
])
```

The same edit over many files: `map_reduce("src/**/*.py", "Add type hints to {{files}}", batch_size=5,
concurrency=3, verify_cmd="pytest -q")`, then `wait([group])`.

A regression in history: `bisect("v1.2.0", "HEAD", "pytest -q tests/test_x.py", parallelism=4)`; add
`auto_fix=True` to start a verified fix job with the guilty diff.

A hard bug or design choice: `consensus("Fix the race in ledger.reserve", ["kimi", "codex", "opencode-go"],
verify_cmd="pytest -q", repo="/src/app")` (costs one job per engine plus one), or a race:
`start_many` of the same task on two engines, then `wait(ids, mode="first_success")`.

A job going wrong: `inspect(id, tail=80, grep="Error")`, then `steer(id, "hint", "use the cache in db.py")`
or `steer(id, "abort")`.

## Example

```
start_many([
  {"task": "Find every caller of parse_date() and list the formats each passes.",
   "kind": "read", "readonly": true, "cwd": "/src/app"},
  {"task": "Make tests/test_sync.py pass without changing its assertions.",
   "kind": "code", "repo": "/src/app", "files": ["src/sync.py", "tests/test_sync.py"],
   "verify": "pytest -q tests/test_sync.py"}
])
wait(["st-1a2b3c", "st-4d5e6f"])
```

CLI equivalent: `subtask start "..." --kind read --readonly --cwd .`,
`subtask wait ID ID`, `subtask result ID --evidence`.
