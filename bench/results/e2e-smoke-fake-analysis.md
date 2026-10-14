Evidence: smoke. Comparator: subtask-auto.

| arm | runs | verified | $ incl. failures+retries | $ unknown | replies | regressions | conflicts | status |
|---|---|---|---|---|---|---|---|---|
| native | 0 | None | None | 0 | 0 | 0 | 0 | {'not_run': 16} |
| parent | 0 | None | None | 0 | 0 | 0 | 0 | {'not_run': 16} |
| subtask-auto | 16 | 0.688 | 5.75 | 0 | 7 | 0 | 0 | {'ran': 16} |
| subtask-fixed:fake | 16 | 0.938 | 5.0 | 0 | 4 | 0 | 0 | {'ran': 16} |

| metric | arm − comparator | tasks | dropped (unknown) | mean diff | 95% CI | negative |
|---|---|---|---|---|---|---|
| verified | subtask-fixed:fake − subtask-auto | 16 | 0 | 0.25 | (0.0, 0.5) |  |
| first_attempt | subtask-fixed:fake − subtask-auto | 16 | 0 | 0.1875 | (-0.1875, 0.5) |  |
| usd | subtask-fixed:fake − subtask-auto | 16 | 0 | -0.0469 | (-0.125, 0.0469) |  |
| wall_s | subtask-fixed:fake − subtask-auto | 16 | 0 | -0.8187 | (-2.1994, 0.355) |  |
| parent_tokens [bytes/3] | subtask-fixed:fake − subtask-auto | 16 | 0 | -60.875 | (-189.875, 82.4375) |  |
| parent_tokens [bytes/4] | subtask-fixed:fake − subtask-auto | 16 | 0 | -45.6875 | (-142.3125, 61.6875) |  |
| parent_tokens [bytes/5] | subtask-fixed:fake − subtask-auto | 16 | 0 | -36.5625 | (-113.9375, 49.4375) |  |
| round_trips | subtask-fixed:fake − subtask-auto | 16 | 0 | -0.75 | (-2.0, 0.75) |  |
