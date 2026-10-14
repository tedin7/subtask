# Client facts (verified 2026-10-14)

Legend: ran = executed on this host in a temp HOME; binary = strings of the installed binary; docs = official page.

| | Claude Code 2.1.296 | Codex 0.162.1 | Kimi Code 2.1.1 | OpenCode 2.0.28 |
|---|---|---|---|---|
| MCP CLI | `claude mcp add/list/remove -s user` (ran) | `codex mcp add/list/get/remove` (ran) | none: edit `~/.kimi-code/mcp.json` or TUI `/mcp-config` (ran) | `opencode mcp add [--global]`, `list`; no remove (ran) |
| Config | `~/.claude.json` mcpServers; project `.mcp.json` | `$CODEX_HOME/config.toml` `[mcp_servers.X]` | `~/.kimi-code/mcp.json` (`$KIMI_CODE_HOME`), project `.kimi-code/mcp.json` | `~/.config/opencode/opencode.json` `mcp.servers.X` {type:"local",command:[..]} |
| Era / version | probes `server/discover` at 2026-07-28, falls back to initialize 2025-11-25 (ran) | initialize 2025-06-18 (ran) | client LATEST 2025-11-25 (binary) | initialize 2025-11-25 (ran) |
| progressToken on tools/call | yes (ran) | sent on tools/list; tools/call not reached (usage limit) (ran) | no: callTool has no onprogress (binary) | yes (ran) |
| Progress rendered | not in `-p` stream-json (ran); TUI: see progress-matrix.md | not checked | no handler | not in `opencode run` (ran) |
| Tool timeout | `MCP_TOOL_TIMEOUT` (~28 h unset), per-server `timeout` ms; hard wall clock (docs) | `tool_timeout_sec` default 60 (docs) | `toolTimeoutMs`, `KIMI_MCP_TOOL_TIMEOUT_MS` (docs) | `timeout` 5000 ms covers tool listing only (docs) |
| Plugin | `.claude-plugin/plugin.json` + `.mcp.json` + `skills/` + `hooks/hooks.json`; marketplace `.claude-plugin/marketplace.json`; `claude plugin validate`, `claude plugin marketplace add`, `claude plugin install X@M` (ran) | `.codex-plugin/plugin.json`; marketplace `.agents/plugins/marketplace.json`; `codex plugin marketplace add`, `codex plugin add X@M` (ran) | `kimi.plugin.json` / `.kimi-plugin/plugin.json`; install only in TUI `/plugins` (docs) | JS/TS plugins in `~/.config/opencode/plugins/`, hooks `tool.execute.before/after`; no bundled MCP (docs) |
| Skills | `~/.claude/skills`, plugin `skills/` | `.agents/skills`, plugin `skills/` | `~/.kimi-code/skills`, `~/.agents/skills` | `~/.config/opencode/skills`, `~/.claude/skills`, `~/.agents/skills` |
| Hooks on MCP tools | PreToolUse/PostToolUse `mcp__.*`, PostToolUse has `duration_ms` (binary) | Pre/PostToolUse for `mcp__server__tool`, no duration, needs trust (docs) | Pre/PostToolUse, PostToolUseFailure (binary) | `tool.execute.before/after` (docs) |

Also found while probing engines (ran): Kimi 2.1.1 refuses `-p` with `--auto`; Codex account at usage limit until 2026-10-20;
OpenCode default OpenRouter model out of credits.
