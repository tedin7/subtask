# subtask

Background sub-agents for any coding agent: Claude Code, Codex, Kimi, OpenCode.
One MCP server (and a twin CLI) to start work in parallel, wait for it with a
single call, and read back a short summary. It picks the engine with the most
free quota, and keeps a monthly spend cap on API credit.

```
start("Fix the flaky test in tests/test_sync.py", repo="/src/app")
  -> st-4f2a91 started · sonnet-api · branch subtask/st-4f2a91
wait(["st-4f2a91", "st-77c0de"])
  -> st-4f2a91 done · sonnet-api · 6m · 0.412$ · success
     st-77c0de done · haiku-api  · 2m · 0.018$ · success
result("st-4f2a91")
  -> ## Summary  (≤10 lines: the answer, files, commits, tests, what is left)
```

## Why

- **Save the caller's context.** A sub-agent's search, test runs and dead ends
  stay in its own session. The caller reads a summary of about ten lines.
- **Fewer tokens spent waiting.** `wait()` blocks until all jobs end, so one
  call replaces a loop of status checks.
- **Use whatever has room.** `engine="auto"` reads the free quota of each
  engine and picks the best fit for the kind of task.
- **Safe by default.** Each writing job gets its own git worktree and branch.
  It never pushes. Bash runs in the Claude Code OS sandbox.

## When an agent should delegate

Delegate:
- two or more independent pieces that can run in parallel;
- long or noisy work whose details you do not need (broad searches, audits,
  refactors across many files, test-and-fix loops);
- an independent reviewer for complex code (concurrency, persistence,
  security, money, logic shared by many callers).

Do it yourself when the change is small, needs your current context, or the
brief would be longer than the work.

These rules are also in the MCP server instructions, so every client reads them.

## Engines

| engine       | pays with                 | good for                     |
|--------------|---------------------------|------------------------------|
| `haiku-api`  | API key                   | reading, search, summaries   |
| `sonnet-api` | API key                   | coding, review (default)     |
| `opus-api`   | API key                   | hard design only             |
| `claude`     | Claude Code login         | coding                       |
| `codex`      | Codex login               | coding                       |
| `kimi`       | Kimi CLI login            | coding, reading              |
| `opencode`   | OpenCode login            | coding, reading              |

`engine="auto"` with `kind` = `read`, `code`, `review` or `hard` picks among the
fitting engines the one with the most free quota (`engines()` shows it):

- API engines: monthly cap minus the local ledger.
- `claude`: the subscription usage endpoint used by Claude Code (undocumented;
  reported as unknown when it fails).
- `codex`: the `rate_limits` block in the newest Codex session file.
- `kimi`, `opencode`: no quota data, treated as half free.

The `*-api` engines run through the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python), with
`max_budget_usd`, `effort`, the OS sandbox and the built-in prompt caching.
The other engines run their own CLI in headless mode.

## Install

```bash
git clone https://github.com/<you>/subtask && cd subtask
python3 -m venv .venv && .venv/bin/pip install -e .
mkdir -p ~/.config/subtask
printf 'ANTHROPIC_API_KEY=sk-ant-...\n' > ~/.config/subtask/env
chmod 600 ~/.config/subtask/env
```

If your key is not scoped to a workspace, add `ANTHROPIC_WORKSPACE_ID=wrkspc_...`.
The OS sandbox needs `bubblewrap` and `socat` on Linux.

### Register the MCP server

Claude Code:
```bash
claude mcp add --scope user --transport stdio subtask -- /path/to/subtask/.venv/bin/subtask-mcp
```

Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.subtask]
command = "/path/to/subtask/.venv/bin/subtask-mcp"
tool_timeout_sec = 660
```

OpenCode:
```bash
opencode mcp add --global subtask -- /path/to/subtask/.venv/bin/subtask-mcp
```

Any agent with a shell can use the CLI instead: `subtask --help`.

## Tools

| tool | what it does |
|------|--------------|
| `start(task, engine="auto", kind="code", repo=None, cwd=None, base="HEAD", readonly=False, max_usd=None, max_turns=60, effort="medium")` | start a job, return its id |
| `wait(ids, timeout=50, any=False)` | block until all (or the first) end, max 600 s |
| `status(id=None)` | one line per job |
| `result(id, full=False)` | the summary, branch and commit count |
| `reply(id, message)` | continue a finished job in the same session |
| `stop(id)` / `cleanup(id)` | stop a job / remove its worktree, keep the branch |
| `spend()` / `engines()` | API spend this month / free quota per engine |

## Settings

| variable | default |
|----------|---------|
| `SUBTASK_HOME` | `~/.local/share/subtask` (jobs, worktrees, ledger) |
| `SUBTASK_ENV_FILE` | `~/.config/subtask/env` |
| `SUBTASK_MONTHLY_CAP_USD` | `180` |
| `SUBTASK_DEFAULT_MAX_USD` | `2` |
| `SUBTASK_MAX_RUNNING` | `4` |

Costs come from the SDK's estimate (`total_cost_usd`), not from billing. A
stopped API job is charged its full budget, because its real cost is unknown.

## License

MIT
