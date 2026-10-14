# Baseline, benefits and trade-offs (2026-10-14)

Machine: 16 CPU, 31 GB, Linux 6.12, Python 3.14. Baseline `3f731f6`.
Suite before: 34 passed (docs/baseline/pytest-before.txt). After: 136 passed
on Python 3.11, 3.12, 3.13 and 3.14; ruff clean.

## Control plane, before vs after (same machine, alternated 3x, 20 reps each)

Raw: `docs/baseline/ab/`. The machine was busy with other test runs, so
absolute numbers are noisy; medians of the three runs:

| metric | before | after | reading |
|---|---|---|---|
| MCP startup to `initialize` reply | 55.8 ms | 47.1 ms | unchanged within noise (the fast path still imports nothing heavy) |
| first `status()` call (imports core) | 99.4 ms | 30.3 ms | faster: active index instead of scanning every job |
| `engines()` | 15.2 ms | 21.6 ms | slower: capacity now carries source, freshness, confidence for 8 engines |
| `pick` cold / warm | 9.7 / 0.09 ms | 19.2 / 0.51 ms | slower: admissibility filters, budget check, history read |
| fixed schema weight | 673 tok | 742 tok | +69 (+10 %): `policy`, `verify`, `evidence`, `since` parameters (bytes/4 estimate) |
| `engines` reply | 105 tok | 164 tok | +59: confidence and age per engine |

Progress overhead (bench/results/progress-overhead-*.json, 25 s fake job, real
server): 5 notifications, +840 bytes on stdout, +0.03 to 0.13 s server CPU;
fast calls (`status`, `start`) unchanged at ~2 / ~4 ms. Only clients that send
a progress token pay it.

History growth: `admission.count()` ~0.2 ms at any history size (before: O(jobs),
each job's status read). Rebuilding a corrupt or missing index: 33 ms at 1k jobs,
433 ms at 10k. `ledger.committed()`: 7 ms at 2k rows, 146 ms at 20k rows in one
month (monthly files bound it; before, one file for all months).

## Benefit cards

| # | Problem observed | Solution | Metric | Baseline | After | Status |
|---|---|---|---|---|---|---|
| B1 | cap of 2 let 3-7 jobs start (12 concurrent callers) | global admission lock + index | jobs started under a barrier | 3-7 | exactly 2 (test, every run) | measured |
| B2 | `step.title` missing from the published schema | context-aware slimming + semantic checker | schema defects | 1 | 0 | measured |
| B3 | money reserved after routing and worktree creation | reserve with the slot, compensate after | wasted worktrees on cap refusal | 1 per refusal | 0 (test) | measured |
| B4 | Kimi 2.1.1 rejected every job (`-p` + `--auto`) | adapter argv from recorded real output | kimi jobs that run | 0 % | 100 % in the live check | measured (1 live job) |
| B5 | Claude Code 2.1.296 refused the new protocol path without caching hints | `ttlMs`, `cacheScope` | tools loaded in a real client | 0 | 10 | measured |
| B6 | Codex wrote with sandbox and approvals bypassed | `workspace-write` + `--add-dir` by default | default Codex write mode | bypass | engine sandbox | verified by argv test; live Codex not run (quota) |
| B7 | no live view while waiting | progress notifications | updates visible during a 18 s wait in Claude Code | 0 | 5 updates in place | measured in the real TUI |
| B8 | "tests pass" was a claim | `verify=` run by subtask | verified results | none | 2/2 live jobs verified on their recorded head | measured |
| B9 | worker could read the whole home | bubblewrap `isolated` | secret in HOME readable by worker | yes | no (test); real Kimi job ran isolated and committed | measured |
| B10 | redaction only `sk-ant` | 13 formats + known values | synthetic secrets masked | 1/12 | 12/12 | measured |
| B11 | quota unknown counted as 50 % | unknown stays unknown, flagged prior | silent assumptions in routing | yes | none: `free_assumed` shown | measured |
| H1 | delegation saves parent context | summary + evidence on demand | parent tokens per accepted task vs parent-only | — | — | **hypothesis, not measured** (live arms not run) |
| H2 | routing by quality/cost/latency beats fixed engines | utility + calibration | verified success per USD | — | — | **hypothesis**: priors only until >=10 verified outcomes per cell |
| H3 | batching beats polling | start_many + wait | round trips per task | fake smoke: 4 per run | — | pipeline checked offline only |

Costs of these changes: the package grew from 1,824 to 6,114 lines (installers included), 13 new core modules,
+10 % fixed schema tokens, +6 ms on `engines()`, +10 ms on a cold pick, a
second protocol era to maintain in the fast server.
