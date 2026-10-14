# Benchmark: does delegating through subtask pay off?

Status, 2026-10-14: live runs so far are small (see the "Live" sections at the end):
too few tasks for any general claim. The corpus is validated, the harness runs end to
end offline and live, and an offline overhead microbenchmark exists.

## 1. Corpus (frozen)

`bench/corpus/manifest.json` (v1, `corpus_hash` recorded). Target: this
repository at `3f731f60ebcacba35c0d92f6cc4b917b836517ce`; each run works on a
private clone of that commit, so job branches never land in the real repo.

24 tasks, 6 per kind, split 2 calibration + 4 test per kind:

| kind | ids | acceptance |
|---|---|---|
| read | r1-r6 | reference answer, required facts as regexes, forbidden regexes; score = facts found / facts; success = 1.0 |
| review | v1-v6 | same, seeded on real defects at 3f731f6: `_slim()` drops `step.title`; MAX_RUNNING check-then-act race; Kimi 2.1 rejects `-p` with `--auto`; serverInfo 0.2.0 vs package 0.1.0; redaction only `sk-ant`; codex write path `--dangerously-bypass-approvals-and-sandbox` |
| code | c1-c6 | hidden pytest file copied in AFTER the worker ends, run with pytest; `must_not_modify` paths |
| hard | h1-h6 | same; cross-process admission race, redaction formats, worktree rollback, reserve at admission, single-source version, ledger month boundary |

`delegation_expected: false` on r2, r5, v4, c2, c5, c6 (one-line lookups or
edits where the brief is as long as the work).

Validation (`python bench/validate_corpus.py`, recorded per task in the
manifest under `validated`, log in `bench/results/corpus-validation.jsonl`):

- code/hard: hidden test FAILS on 3f731f6 and PASSES with the reference patch
  (`bench/corpus/reference/*.patch`, never shown to workers); the patch keeps
  the target's own suite green and respects `must_not_modify`. 12/12 pass.
- read/review: reference answer scores 1.0, empty answer fails. 12/12 pass.
- The target suite has timing tests that flake under load; failed tests are
  re-run once and reported as `flaky`, a test failing twice is a regression.

Changing a task or an acceptance file changes `corpus_hash`; `e2e.py` refuses
to run until the corpus is validated again.

## 2. Arms and ablations

| arm | what runs |
|---|---|
| `parent` | parent client alone: `claude -p --output-format json` with `--disallowedTools Task Agent`, or `codex exec --json` |
| `native` | parent with native sub-agents allowed |
| `subtask-fixed:<engine>` | parent delegates through the subtask MCP server to one engine |
| `subtask-auto` | same with `engine="auto"` |

Ablations (subtask arms): `--batching on|off` (start_many + one wait vs start +
status polling), `--summary summary|full` (`result` vs `result(full=true)`),
`--progress on|off` (progress token; off sets the reporter intervals to 1e9 s),
`--narrator on|off` (step() calls).

## 3. Protocol

- Frozen run config `bench/configs/*.json`: arms, repetitions, seed, split,
  budgets, thresholds, ablations. The effective config (file + CLI overrides)
  and the corpus hash are hashed; the hash is written to `lock.json` in the
  result folder before any run. A changed hash on a folder that has results
  is refused (tested: `bench/results/e2e-hash-refusal.txt`).
- Splits: calibration tasks may be used to tune prompts and thresholds and are
  never reported; claims come from `--split test` only.
- Order: every (task, repetition) block contains every arm once, arm order
  shuffled per block, blocks shuffled, all from the config seed.
- Global caps: max total USD (each run's upper bound is reserved before it
  starts, actual cost settled after; unknown cost is charged at the bound),
  max wall time, max concurrency (retries run inside their slot). When a cap
  is hit the remaining runs are recorded `aborted` (tested:
  `smoke-fake-capabort`).
- Per run record: arm, task, rep, versions (subtask, `claude --version`,
  `codex --version`), timestamps, wall time, parent tokens (exact from the
  client JSON usage, else null), parent USD, worker cost and attempts from
  subtask's job files, round trips, replies, verified success, first-attempt
  success, regressions (full target suite), merge conflicts between job
  branches, must-not-modify violations. Unknown cost is null, never 0.
- Analysis (`bench/analyze.py`): repetitions are averaged inside (task, arm);
  the task is the unit. Paired per-task differences against the comparator,
  bootstrap CI over tasks. Costs include failures and retries; tasks with an
  unknown cost are dropped from that comparison and counted. Estimated parent
  tokens (bytes/3, /4, /5) are shown as a sensitivity band, never mixed with
  exact usage. Unfavourable point estimates and CIs are listed as negative
  results. Output: markdown table + raw JSON.
- Claim thresholds (config): at least 16 test tasks paired; success
  non-inferiority margin 0.10; 95 % CI.

## 4. Offline results (smoke, not evidence)

`python bench/e2e.py --config bench/configs/smoke-fake.json --fake`: 64
planned runs on the 16 test tasks; the 32 subtask runs ran through the real
fastserver and the fake engine, the 32 parent/native runs are `not_run`.
Scripted outcomes (70 % success, 20 % fail then success after one reply, 10 %
fail) were recovered exactly: 21 success verified, 5 verified after a reply,
6 failures not verified; 0 regressions, 0 must-not-modify violations, 0
errors. Median 4 round trips, ~2 KB read by the scripted parent, 4 progress
notifications per run. `subtask-auto` cannot route to the test engine; its
would-be choice is recorded (`haiku-api` for read, `sonnet-api` for code and
review, `opus-api` for hard) and the job runs on the fake engine. The
differences between arms in `bench/results/e2e-smoke-fake-analysis.md` only
reflect the scripted outcomes. `smoke-fake-ablations` (status polling, full
results, no progress token, narrator calls) ran 4/4.

## 5. Progress and hot-path overhead (offline)

`python bench/progress_overhead.py` (25 s fake job, real fastserver, production
intervals 2 s / 10 s), `bench/results/progress-overhead-*.json`:

| | with progressToken | without |
|---|---|---|
| notifications during a 25 s wait | 5 | 0 |
| server stdout bytes for the wait | 1293 | 453 |
| server CPU for the whole job | 0.15-0.25 s | 0.12 s |
| status latency, median / p95 | 1.9 / 3.0 ms | 2.0 / 7.1 ms |
| start latency, median / p95 | 3.9 / 5.4 ms | 4.4 / 10.5 ms |

Progress costs about 0.8 KB and up to ~0.1 s of server CPU per 25 s of
waiting, and no measurable latency on fast calls (differences are within
run-to-run noise; two runs gave CPU 0.15 and 0.25 s).

| history (jobs / ledger rows) | admission.count, index present | rebuild from corrupt index | ledger.committed |
|---|---|---|---|
| 0 / 0 | 0.24 ms | 0.6 ms | 0.18 ms |
| 1k / 2k | 0.18 ms | 33 ms | 7.1 ms |
| 10k / 20k | 0.16 ms | 433 ms | 146 ms |

`admission.count()` is O(active) as designed. `ledger.committed()` is
O(rows in the month): ~146 ms at 20k rows, and it runs under the ledger lock
on every admission of a paid job. Not a problem at today's volumes; a running
total per month file would make it O(1).

## 6. Running live (when a budget is authorized)

1. `python bench/validate_corpus.py` (must report 24 ok).
2. Bound: `python bench/e2e.py --config bench/configs/pilot.json --split test --dry-run | tail -1`.
   Today: 192 runs (16 tasks x 4 arms x 3 reps), `usd_upper_bound` 576 $
   (parent budget = task `max_usd` via `--max-budget-usd`, plus worker
   `max_usd` x 2 for one retry on subtask arms). The config caps total spend at
   250 $; runs past the cap are recorded `aborted`, so lower the repetitions or
   arms if the full plan must complete.
3. Calibration first (96 runs, bound 288 $, same 250 $ cap): `--split calibration --dry-run`, then
   `python bench/e2e.py --config bench/configs/pilot.json --split calibration --live --budget-usd <B>`.
4. Test: `python bench/e2e.py --config bench/configs/pilot.json --split test --live --budget-usd 250`.
   `--live` is refused unless `--budget-usd` covers min(bound, cap).
5. `python bench/analyze.py bench/results/e2e/pilot-test-live/runs.jsonl --json out.json --md out.md`.
6. Ablations: one extra config per ablation (a new name, so the lock does not
   mix hashes), e.g. `--batching off`.

Codex as parent bills plan quota, not dollars: its parent cost is recorded as
null and analysed as unknown, never as 0.

## 7. Not run

- Every live arm (`parent`, `native`, `subtask-fixed:<real engine>`, `subtask-auto` on real engines).
- Any measurement of parent context saved, dollars or latency against doing the work directly.
- Exact parent tokens: only the client JSON can give them, and no client ran.

## Live mini run (2026-10-14, owner cap 5 USD)

Config `bench/configs/mini-5usd*.json`: tasks r3 (read) and c3 (code), 1 repetition, parent
Claude Code 2.1.296 with `--model sonnet`, subtask arm on `sonnet-api`. The first subtask attempt
did not reach the server (harness looked up `subtask-mcp` on PATH only; fixed) and is excluded.

| task | arm | verified | parent $ | worker $ | total $ | wall s | parent cache read tok | subtask calls |
|---|---|---|---|---|---|---|---|---|
| r3 | parent | yes | 0.070 | - | 0.070 | 9.5 | 32,055 | 0 |
| r3 | subtask sonnet-api | yes | 0.086 | 0.083 | 0.169 | 25.7 | 129,143 | 13 |
| c3 | parent | yes | 0.078 | - | 0.078 | 11.5 | 79,603 | 0 |
| c3 | subtask sonnet-api | yes | 0.100 | 0.064 | 0.164 | 36.1 | 181,430 | 14 |

Reading (2 tasks, 1 rep: no statistical claim): on small tasks delegating cost about 2x the
money, 3x the time and more parent context, with the same verified success. The parent made
13-14 subtask calls instead of start + one wait: the skill was not loaded in `-p` mode, only the
short server instructions. Real API spend: 0.147 $ (workers); parent costs are Claude subscription
estimates.

## Live rerun on 0.2.7 and hard tasks (2026-10-14, owner budget 20 USD)

The "13-14 subtask calls" reported for the first mini run were a counting error: the server polls
`core.wait` every second inside ONE `wait` tool call, and each poll was logged as a round trip
(fixed in 0.2.8: one tool call, one count). The parent's real turns are below (`turns`, from
Claude's own JSON).

| task | arm | verified | parent turns | parent $ | worker $ | total $ | wall s | parent cache read tok |
|---|---|---|---|---|---|---|---|---|
| r3 (read) | parent | yes | 2 | 0.071 | - | 0.071 | 8.9 | 32,061 |
| r3 (read) | subtask sonnet-api | yes | 6 | 0.088 | 0.089 | 0.177 | 34.6 | 129,670 |
| c3 (code) | parent | yes | 4 | 0.073 | - | 0.073 | 11.7 | 78,901 |
| c3 (code) | subtask sonnet-api | yes | 7 | 0.096 | 0.092 | 0.188 | 36.1 | 155,931 |
| h2 (hard) | parent | **no** | 7 | 0.127 | - | 0.127 | 39.0 | 158,570 |
| h2 (hard) | subtask sonnet-api | yes | 7 | 0.103 | 0.107 | 0.210 | 59.8 | 157,347 |
| h3 (hard) | parent | yes | 5 | 0.090 | - | 0.090 | 18.5 | 106,754 |
| h3 (hard) | subtask sonnet-api | yes | 7 | 0.098 | 0.073 | 0.171 | 34.7 | 156,245 |
| h5 (hard) | parent | yes | 6 | 0.099 | - | 0.099 | 31.1 | 133,158 |
| h5 (hard) | subtask sonnet-api | yes | 6 | 0.091 | 0.084 | 0.175 | 51.6 | 130,269 |

Reading (5 tasks, 1 repetition each: indicative only, no statistics):
- Small tasks: delegating adds about 3-4 parent turns (start, wait, checking the diff), and each turn
  re-reads ~20k tokens of context: about 2.5x the money and 3x the time for the same result.
- Hard tasks: the parent's context is about the same with or without subtask (it works its way up to
  similar turn counts either way); money about 1.7x, time about 1.7x; verified success 3/3 with subtask,
  2/3 for the parent alone (h2). One task is not evidence of a quality advantage.
- Where delegation should win (parallel independent pieces, long noisy work) is not in this corpus yet:
  every task here is a single short sequential job. That is the next benchmark to build.
Real API spend for these runs: 0.53 USD (workers); parent costs are Claude subscription estimates.
