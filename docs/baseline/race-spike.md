# Spike: cross-process MAX_RUNNING (R04), 2026-10-14, HEAD 3f731f6

12 processes, `core` pre-imported, released by a barrier, each calls
`core.start(engine="kimi")` with a fake `kimi` (sleep 6), `SUBTASK_MAX_RUNNING=2`.

| run | started |
|---|---|
| 1 | 7 |
| 2 | 4 |
| 3 | 6 |
| 4 | 3 |
| 5 | 5 |

With import inside the barrier (staggered start) 3/3 runs started exactly 2:
the race needs truly concurrent callers. Confirmed: the cap is not global.
