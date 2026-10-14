# Progress in the chat: what each client really shows (2026-10-14)

Three separate facts per client: the server **produced** progress, the client **received** it
(wire capture through a transparent stdio tap), and it was **visible in the chat during the call**
(screen captures of the real TUI in tmux, every second).

| Client | Version | Sends progressToken | Received (wire) | Visible in chat during the call | Evidence | What to use |
|---|---|---|---|---|---|---|
| Claude Code | 2.1.296 TUI | yes (`_meta.progressToken`, modern 2026-07-28) | yes | **yes**: under the tool call, `⎿ Subtask · 00:07 · 0/3 ended — st-… running tests: pytest -q …`, updated in place | `progress-evidence/claude-code-2.1.296/` | native progress |
| Claude Code | 2.1.296 `-p` stream-json | yes | yes | no (headless output has no progress) | client research | `wait(since=…)` fallback |
| OpenCode | 2.0.28 TUI | yes (+ `ai.opencode/sessionID`) | yes (5 notifications) | **no**: only `› subtask.wait [timeout=120]` while waiting | `progress-evidence/opencode-2.0.28/` | fallback: `wait(timeout=…, since=cursor)` deltas; the skill tells the agent to relay changes |
| Codex | 0.162.1 | on tools/list yes; tools/call not reached | not tested | **not_tested**: account at usage limit until 2026-10-20 | client research | retest after the reset |
| Kimi Code | 2.1.1 | no (callTool has no progress handler, binary strings) | n/a | no | client research | fallback |

The fallback is not streaming: each `wait(..., since=cursor)` is one more round trip. Cost per
round trip: one tool call plus a reply of 2-6 lines (about 60-200 tokens, bytes/4 estimate).

Other MCP servers: subtask cannot see inside them. The Claude Code plugin (opt-in) adds a
PostToolUse hook that reports server, tool, duration and outcome for every `mcp__*` call
after it ends; it does not stream and shows only what the client observes.

How to reproduce (no API spend: the `demo` engine runs nothing):
`SUBTASK_DEMO=1` in the server env, then ask the agent: start_many with engine `demo`
tasks "success A", "fail B", "silent C", then one wait.
