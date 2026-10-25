# Progress and delivery: what each client really shows (2026-10-25)

Three separate facts per client: the server **produced** progress, the client **received** it
(wire capture through a transparent stdio tap), and it was **visible in the chat during the call**
(screen captures of the real TUI in tmux, every second). A fourth fact matters for background
work: whether a notification can **wake an idle model** outside a tool call. Codex 0.164.0 cannot
(source below); for the other clients we found no supported path, but that column is not a live
wake test.

| Client | Version | Sends progressToken | Received (wire) | Visible in chat during the call | Can wake an idle turn | Evidence | What to use |
|---|---|---|---|---|---|---|---|
| Claude Code | 2.1.296 TUI | yes (`_meta.progressToken`, modern 2026-07-28) | yes | **yes**: under the tool call, `⎿ Subtask · 00:07 · 0/3 ended — st-… running tests: pytest -q …`, updated in place | not tested; no supported path found | `progress-evidence/claude-code-2.1.296/` | native progress; blocking `wait` |
| Claude Code | 2.1.296 `-p` stream-json | yes | yes | no (headless output has no progress) | not tested; no supported path found | client research | `wait(mode="any", timeout=60, since=…)` fallback |
| OpenCode | 2.0.28 TUI | yes (+ `ai.opencode/sessionID`) | yes (5 notifications) | **no**: only `› subtask.wait [timeout=120]` while waiting | no supported path found | `progress-evidence/opencode-2.0.28/` | fallback: `wait(timeout=…, since=cursor)` deltas; the skill tells the agent to relay changes |
| Codex | 0.164.0 | **no** on `tools/call` (source: no token is attached) | n/a: Subtask sends progress only when a token is present | **no** | **no** (source, see below) | local `codex-cli 0.164.0` + source tag `rust-v0.164.0` (`6aaac694`) | `wait(mode="any", timeout<=60, since=cursor)`; no promise of a wake |
| Kimi Code | 2.1.1 | no (callTool has no progress handler, binary strings) | n/a | no | no handler/API | client research | fallback |

The fallback is not streaming: each `wait(..., since=cursor)` is one more round trip. Cost per
round trip: one tool call plus a reply of 2-6 lines (about 60-200 tokens, bytes/4 estimate). Codex
cursor waits are capped at 60 s by the server (`progress.wait_budget`) so they fit the client's
default MCP tool timeout; an ordinary wait (Claude Code) can still block for 600 s.

## Codex 0.164.0: no out-of-request wake

The installed binary is `/root/.codex/packages/standalone/current/bin/codex`, `codex-cli 0.164.0`;
the source inspected is the official tag `rust-v0.164.0` (peeled commit
`6aaac694803004a63c6f393d153c96d22521a6b9`). No model was run and no provider call was made.

- `codex-rs/rmcp-client/src/logging_client_handler.rs:63-140` handles `on_progress`,
  `on_resource_updated`, `on_resource_list_changed`, `on_tool_list_changed`, `on_prompt_list_changed`
  and `on_logging_message`. Every one only writes a `tracing` line (`info!`/`warn!`/`debug!`). Not one
  submits a turn or injects a model item.
- `codex-rs/core/src/session/handlers.rs:443-742` is the session submission loop. Turns start only
  for an explicit `Op::TurnInput` / `Op::RecoverTurn` (or an agent mailbox wake). There is no MCP
  notification branch that reaches it.
- `codex-rs/core/src/session/inject.rs:12-38` (`inject_if_running`) injects only into an **active**
  turn and returns `Err` when there is none. `inject_no_new_turn` (`:173-200`) records items without
  starting a turn. So an idle session is not woken.
- `codex-rs/core/src/tasks/mod.rs:466-478` (`maybe_start_turn_for_pending_work`) wakes an idle
  session only for pending **mailbox** items (agent messages / durable sleep); MCP notifications do
  not populate that mailbox.
- `codex-rs/rmcp-client/src/rmcp_client.rs:832-900` builds `tools/call` without a progress token.
  `Subtask` therefore sends no progress notification to Codex at all, even though the server can.
- `codex-rs/codex-mcp/src/rmcp_client.rs:113` sets `DEFAULT_TOOL_TIMEOUT = 300 s`. The subtask
  installer writes `tool_timeout_sec = 660` (`subtask/install/clients.py:19`), but that only extends a
  call; it does not create a call after the turn ended.

The exact limit: **Codex 0.164.0 cannot be woken by an MCP notification, progress or log message
outside a request. There is no supported server push into an idle model turn.** A bridge based on
the experimental app-server methods (`thread/queue/add`, `thread/queue/start`, `turn/start`; see
`codex-rs/app-server-protocol/src/protocol/common.rs:643-678` and `:1070`) would inject a **new user
turn** into a live thread, not deliver a notification. It needs the live thread id, a connection to
the app-server owning it, and explicit user consent, and it cannot be proven without launching
Codex and injecting input. It is therefore not implemented and must not be advertised.

## The supported delivery path

Keep the request open; do not rely on a notification:

```
# immediately after start/start_many, in the same turn
wait(ids, mode="any", timeout=60, since="")
# it returns at the first job end (or after 60 s with "still running" and cursor=)
# remove the ids it reports as ended, pass cursor= back, repeat for the rest
```

This is a server-side blocking wait, not `sleep 600` plus `status()`. It returns as soon as work
ends, and a 60 s cap keeps every round trip inside Codex's default tool timeout. It is **not**
equivalent to waking an inactive session: if the agent ends its turn, the next step needs a new
user message.

How to reproduce the defect without spending anything: start a job, send no further request, and
watch stdout while the worker finishes. Codex receives no unsolicited message (it has no open
request); `wait` is the only thing that returns the result. The tests
`test_codex_gets_no_out_of_band_wake_when_a_job_finishes` and
`test_cursor_wait_returns_at_the_first_job_end` capture both halves.

Other MCP servers: subtask cannot see inside them. OpenCode 2.x: the plugin `plugins/opencode/subtask-progress.ts`
(verified loaded in 2.0.28) records server/tool, duration and status of every MCP call to
`~/.local/share/subtask/opencode-tool-timing.jsonl`; OpenCode offers plugins no way to write in the chat.
 The Claude Code plugin (opt-in) adds a
PostToolUse hook that reports server, tool, duration and outcome for every `mcp__*` call
after it ends; it does not stream and shows only what the client observes.

How to reproduce (no API spend: the `demo` engine runs nothing):
`SUBTASK_DEMO=1` in the server env, then ask the agent: start_many with engine `demo`
tasks "success A", "fail B", "silent C", then one wait.
