# Plan compliance check — subtask v0.3.0 (branch main, commit 8203a6c)

Read-only audit of code and tests (README not trusted as evidence). Verdicts: DONE / PARTIAL / MISSING.

| Item | Verdict | Key evidence |
|---|---|---|
| M1.1 isolation/branch/env/files_modified/integrate | DONE | core.py:25,94,107-108,210-215,652-659; worker.py:286-295; config.py:11,28; integrate.py:54,113-132 |
| M1.2 context_refs + repomap | DONE | context.py:40-97 (`<subtask_context>` :97), 103-140 (AST :111-118, regex :23-31, HEAD-keyed cache :123-140); core.py:125-135 |
| M1.3 verify/retries/snapshot/rollback/adversarial | DONE | core.py:91,95,109-112,136-164; worker.py:197-215; verifier.py:37-68 (snapshot/restore), 71-76 (failure output in retry), 83-115 (rollback), 125-149 (Breaker gate) |
| M1.4 wait modes/watchdog/steer/inspect/payload | PARTIAL | core.py:440,498,511-563,743-809; watchdog.py:43-55; `budget_remaining_usd` absent everywhere |
| M2.1 DAG id/depends_on/acyclic/auto-start/lazy refs/skip | DONE | groups.py:64-94 (validate+acyclic), 156-203 (advance), 134-142 (_substitute), 176-178; core.py:579-584 |
| M2.2 blackboard + wait(since) deltas | DONE | groups.py:49-50,113,238-243,280-299; core.py:409-437,547-551 |
| M2.3 tiers / 75% degrade / engine stats | PARTIAL | core.py:26-27,113-114,299-304; router.py:72-77,126-138; telemetry.py:79-106 (`engine_stats.json` write-only, "informational") |
| M3.1 map_reduce group id for wait | DONE | map_reduce.py:23-72; groups.py:103,118; core.py:443-457 |
| M3.2 persistence / crash recovery | DONE | jobs.py:31-72,99-142; groups.py:7-8,206-214; events.py:27-28; test_steer_wait.py:148-178 (new server process finds jobs) |
| M3.3 engrams fail-then-pass/top-3/cap-50-LRU/dedup | DONE | memory.py:22-23,88-116 (fail-then-pass),119-145 (top-3 by path scope + LRU touch),46-50 (cap),104-113 (dedup) |
| M4 OpenCode plugin | DONE | plugins/opencode/subtask-progress.ts:49-66 (DAG/queued/deps, verify retries, resumed, map_reduce progress, budget); test_board.py:51-66 |
| M5 README + retrocompat | DONE | README.md:81-110 (all 14 tools, main params); tools.json:5-381; core.py:495,409-420 (since cursor); fastserver.py:64-70,179 (progressToken); ledger.py:127-154 (spend cap); engines/registry.py:34-37 + demo.py (SUBTASK_DEMO=1, untested) |

## PARTIAL / MISSING detail

### M1.4 — wait payload guaranteed fields (PARTIAL)
- `cost_usd`, `verification`, `files_modified` appear only conditionally and as free text inside the `result()` string (core.py:362 paid-only "0.500$", :376-377+:632-647 only when `verify` set, :625-629+:652-659 only for worktree/branch jobs), not as structured always-present fields.
- `budget_remaining_usd` is **MISSING** entirely — no occurrence in `subtask/` (grep 0 hits). Closest: `budget_warning` (core.py:247,279,370-371) and the `engines`/`board` budget lines (core.py:841-843, 960, 963-968).
- Everything else in M1.4 is DONE: modes all|any|first_success with cancellation of losers (core.py:511-563, groups.py:217-233), stall watchdog with `stall_timeout` + "same error 3 times" (watchdog.py:43-55, core.py:536,544-545), steer hint/abort/retry (core.py:743-782, tests test_steer_wait.py:98-132), inspect tail/grep (core.py:785-809).

### M2.3(i) — engine_stats.json drives auto choice (PARTIAL)
- `engine_stats.json` exists and tracks per-repo per-engine runs/verified/passed/usd with `pass_rate` and `avg_usd` (telemetry.py:79-106, written from worker.py:329-333), but it is **never read** — its own docstring says "informational".
- The functional goal is still met by a different mechanism: the auto tier learns P(success)/median cost from telemetry rows (router.py:72-77, 126-138), test-covered (test_routing_results.py:76-87).
- Also note: the 75% degrade + `budget_warning` is DONE (core.py:27,299-304; test_context_dag.py:117-123), tiers DONE (core.py:26,113-120; router.py:107-109).

## Notable caveats on DONE items (not defects against the plan)
- M1.1: `SUBTASK_PORT`/`TMPDIR` are set only for worktree jobs; `TMPDIR` skipped under bubblewrap `isolated` policy (worker.py:289-294). Post-integration verify runs only in the reconciliation (auto_resolve) job's gate, not on the clean apply path (integrate.py:121,128). `files_modified`/`diff_stat` are text lines, asserted via `result()` not `wait()` (test_worktree_verify.py:46).
- M1.2: `git:diff` captures unstaged only (context.py:86). Untested: `git:diff:<ref>` positive case, `subtask:<id>:diff`, whole-file `file:path`, literal `<subtask_context>` tag, HEAD-change cache invalidation, Go/Rust regexes.
- M1.3: snapshot/rollback apply to worktree jobs only (verifier.py:104, worker.py:211-212); retry prompt carries the verify command's stdout/stderr tail (verifier.py:71-76), not the agent's own stream.
- M2.1(e): the marker is the row's `why` string (`skipped_dependency_failed: <dep>`) with `state="skipped"` (groups.py:176-178), not a dedicated state; inline `subtask:<alias>:...` refs in the task text get alias→job-id rewrite only — content injection requires `context_refs` (groups.py:134-142 vs context.py:54-68).
- M5: README documents all 14 tools and the main params; minor omissions: `wait.results`, `map_reduce` `repo`/`cwd`/`engine`/`kind`/`max_usd`, `step` `repo`/`lang`. `SUBTASK_DEMO=1` demo engine (engines/registry.py:34-37, demo.py) has no test.

## Test state
`python -m pytest tests/ -q`: **202 passed, 13 failed, 4 deselected**. All 13 failures are in `tests/test_install.py` (JSONDecodeError from shelling out to real client CLIs `claude`/`codex`/`opencode` absent in this sandbox) — environmental, unrelated to the plan features. One order-dependent flake observed: `test_engine_stats_are_kept_per_repo` fails under `-x` after test_install.py runs, passes standalone and in a full plain run.
