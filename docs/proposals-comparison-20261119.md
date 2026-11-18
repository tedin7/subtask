# Three offline comparisons (T-1002920, T-1002921, T-1002924), 19/11/2026

All three are measurements only: nothing in the product changes and no proposal is adopted. All three
run offline with no model call, so the cost is 0 USD (model and resources are untouched). What they do
NOT measure is said in each section.

## 1. Setup recoverable by semantic blocks (T-1002920)

`PYTHONPATH=. python bench/install_granularity.py` drives the real installer code (`installer._apply`,
`fileops.snapshot`) on a throw-away HOME that already holds user configuration (a kimi `mcp.json` with
another server, an `opencode.json` with a theme and another server). Four operations in two groups
(per client: MCP entry, then skill). Three reproducible faults:
F1 `opencode.json` with comments (the product's own refusal), F2 write error on the kimi skill, F3 write
error on the last operation. After the fault: the file is repaired and the setup is retried.

| fault | strategy | recovered | ops first+retry | files still changed after the fault | failed unit restored byte for byte |
|---|---|---|---|---|---|
| F1 | command | yes | 3+2=5 | 2 | yes |
| F1 | program | yes | 3+4=7 | 0 | yes |
| F1 | group | yes | 3+2=5 | 2 | yes |
| F2 | command | yes | 2+3=5 | 1 | yes |
| F2 | program | yes | 2+4=6 | 0 | yes |
| F2 | group | yes | 2+4=6 | 0 | yes |
| F3 | command | yes | 4+1=5 | 3 | yes |
| F3 | program | yes | 4+4=8 | 0 | yes |
| F3 | group | yes | 4+2=6 | 2 | yes |

Time is a few ms per scenario for every strategy (file edits only), so it does not separate them.
Reading: the whole program leaves the machine exactly as before (zero partial files) but redoes every
operation on retry (5-8 ops against 5-6); single commands redo the least but leave the system half
configured and need the operator to know which part is missing; the group (one client at a time, what the
installer already does) keeps finished clients and restores the failed one, a middle point: 5 to 6 ops
and a half-configured machine only at client granularity, never inside a client. The proposal is not
adopted: the current installer already is the `group` arm.
Limit: operations are local file edits; a real network step (`uv tool install`) would make retries of
the whole program costlier, which this run does not show.

## 2. Compact context with recoverable evidence (T-1002921)

`python bench/context_compaction.py --jobs 12`: the 12 longest real job sessions on this machine
(1942 tool calls, opencode-go and codex), the same participants for every arm; evidence = the exact
output of the 64 failing calls (non-completed status or Traceback / FAILED / ERROR / non-zero exit).

| arm | tokens carried (chars/4) | proofs recovered exactly | tokens per recovered proof |
|---|---|---|---|
| full history | 4,742,330 | 64 / 64 | 74,099 |
| summary (first 8 + last 8 calls, outputs clipped to 300 chars) | 14,726 | 19 / 64 | 775 |
| state with pointers (one line per call plus `job:LINE`) | 70,874 | 64 / 64 (pointer read back, sha256 equal) | 31,106 including the reads |

Reading: the summary is cheapest per proof but loses 70 percent of the exact proofs; the state with
pointers keeps every exact proof at 1.5 percent of the full-history size. The reads cost 1.9M tokens only
because every pointer was followed in full; reading just the needed range is cheaper and not measured.
Limit: success of the task and cost per successful result need model runs (not made): this measures the
size and the recoverability of the proof, the two inputs to that cost.

## 3. Localization guided by callers (T-1002924)

`python bench/localization_callers.py --n 20 --k 3` (and `--k 5`): the 20 newest `fix:` commits of this
repository that change 1-6 files under `subtask/`. The repository is read at the parent of each fix; the
problem text is the commit subject; truth = files the fix really changed.

| k | arm | precision | recall | changed files missed | fixes fully covered (of 20) |
|---|---|---|---|---|---|
| 3 | words | 0.283 | 0.608 | 13 | 9 |
| 3 | callers | 0.333 | 0.683 | 10 | 12 |
| 5 | words | 0.190 | 0.675 | 11 | 11 |
| 5 | callers | 0.190 | 0.633 | 11 | 11 |

Reading: with a short list (k=3) following callers/callees finds 3 more complete fixes and misses 3
fewer related files; with k=5 the advantage disappears (11 against 11). N=20 and the subject is a short
query: a hint, not a proof.
