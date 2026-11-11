# Test-efficacy benchmark: archive limit (T-21864)

The benchmark on the efficacy of generated tests needs the historical candidate
commits `371aae8` and `459a75d` (arms `parent` and `subtask:opencode-go` of the
long run `bench/results/long/2026-10-14T174749.jsonl`).

Checked on 2026-11-11, no API calls, no new generation:

- `git cat-file -t` in `/root/subtask` (all its objects): neither id exists.
- 61 other git checkouts under `/root/wt`, `/root/tmp-agenti` and `/root`
  (depth up to 3): neither id exists.
- GitHub `repos/tedin7/subtask/commits/<id>`: HTTP 422 "No commit found" for both.
- Only the final answers of the two runs survive, in the jsonl above. They say
  which files changed, not the contents, so the generated tests cannot be rebuilt.

Limit: no archive with a verified identity exists, so no verdict on test efficacy
can be given. A new benchmark must start from fresh, archived candidates (commit
ids kept on a pushed branch before the run) and an independent protocol.
