---
name: subtask
description: Delegate work to background sub-agents through the subtask MCP tools (or the `subtask` CLI). Use when there are 2+ independent pieces to run in parallel, long or noisy work whose details you do not need, or complex code that needs an independent reviewer. Not for small edits.
---

# subtask: delegate cheaply

Every token you spend on delegation must be cheaper than doing the work
yourself. Follow these rules.

## Announce every significant step

Before each significant step (new phase, choice of approach, a change with
risk, a merge or release) call `step(title, path, how, decisions)` and show
the returned text to the user verbatim, in your chat. Haiku, a model that did
not plan the step, reads your declaration and the repo state and announces it
in plain words. Put an absolute path in `path` (or pass `repo`) so it reads
the right repository. About 3 s and under 1 cent.

Falsification is separate: when a complex implementation is finished
(concurrency, saved data, security, money, shared logic, a part broken before),
start a sub-task on `sonnet-api` that tries to make it fail. Not on every step. CLI: `subtask step "..." --path ... --how ... --decision ...`.
Small edits and reads are not steps.

## Delegate or not

Delegate when one of these is true:
- 2 or more independent pieces can run at the same time;
- the work is long or noisy and you only need the outcome (broad search,
  audit, refactor over many files, test-and-fix loop);
- complex code needs a reviewer who did not write it (concurrency, saved data
  or migrations, security, money, logic shared by many callers).

Do it yourself when the change is small, needs what you already have in your
context, or the brief would be longer than the work.

## The cheap call pattern

1. One call to start everything: `start_many([...])`, or `start(...)` for one job.
2. One call to collect: `wait(ids)`. It blocks until all jobs end and returns
   each summary. Do not poll `status()` in a loop.
3. Only if a summary is not enough: `result(id, full=True)`.
4. To correct a finished job: `reply(id, "also handle X")`. It keeps the
   sub-agent's context, so the brief is not sent again.

## Writing the brief

- State the goal, the done condition and how to verify it, in a few lines.
- Pass paths in `files=[...]`. Never paste file contents: the sub-agent reads them.
- Set `kind`: `read` (search, summarize: cheapest), `code`, `review`, `hard`.
- Set `readonly=True` for questions and audits. Use `repo=` for changes: the job
  gets its own worktree and branch `subtask/<id>`, commits there, never pushes.
- Leave `engine="auto"`: it picks the fitting engine with the most free quota.
  Call `engines()` only when you need to see quotas or spend.

## After the job

- Review the branch diff yourself before merging: `git diff <base>..subtask/<id>`.
- Merge the exact commits you reviewed, then `cleanup(id)`.
- If the summary says something failed, decide: `reply()` once with a precise
  fix, or take it over. Do not loop.

## Example

```
start_many([
  {"task": "Find every caller of parse_date() and list the formats each passes.",
   "kind": "read", "readonly": true, "cwd": "/src/app"},
  {"task": "Make tests/test_sync.py pass without changing its assertions. Verify with pytest tests/test_sync.py.",
   "kind": "code", "repo": "/src/app", "files": ["src/sync.py", "tests/test_sync.py"]}
])
wait(["st-1a2b3c", "st-4d5e6f"])
```

CLI equivalent: `subtask start "..." --kind read --readonly --cwd .`,
`subtask wait ID ID`, `subtask result ID`.
