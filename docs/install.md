# Installing subtask

The Python distribution is **`subtask-mcp`**. The PyPI name `subtask` belongs to an
unrelated project: never run `pip install subtask`.

## Where it is published (2026-10-14)

| Channel | State |
|---|---|
| GitHub release | `v0.2.11` with wheel and sdist: https://github.com/tedin7/subtask/releases |
| PyPI | `subtask-mcp` 0.2.3: `uv tool install subtask-mcp` |
| MCP Registry | `io.github.tedin7/subtask` 0.2.3 |
| Claude Code / Codex marketplaces | in this repository (`claude plugin marketplace add tedin7/subtask`); no official catalog listing |

## Clean machine

```sh
sh scripts/install.sh              # installs the tool, prints the plan for every detected client
sh scripts/install.sh --yes        # same, and applies it
sh scripts/install.sh --install-uv # also installs uv with its official installer if missing
```

It runs `uv tool install "git+https://github.com/tedin7/subtask@${SUBTASK_REF:-v0.2.11}"`
(falls back to `pipx`), then `subtask install --client all`.

## Automatic install (any client)

```sh
subtask install --client all          # plan only: prints exact commands and file edits, changes nothing
subtask install --client all --yes    # apply
subtask doctor --client all           # app, config entry, server handshake (initialize + tools/list)
subtask uninstall --client all --yes  # remove only what subtask install wrote
```

Options: `--client claude|codex|kimi|opencode|all` (`all` = clients found on PATH;
missing apps are never installed), `--scope user|project`, `--json`, `--force`
(replace a `subtask` entry that is not ours), `--command PATH` (server to register;
default: the `subtask-mcp` next to the Python running `subtask`).

Every file edit leaves `<file>.subtask-bak-<timestamp>`, writes atomically and keeps
the file mode. What was written is recorded in `~/.local/share/subtask/installs.json`;
uninstall removes an entry or skill only if it is still exactly what was written,
otherwise it reports it and leaves it. A failed step rolls back every file of that client.

## Claude Code

One route: the plugin. It brings the MCP server, the skill and a timing hook, so the
server is registered once.

- Automatic: `subtask install --client claude --yes`. It runs `claude plugin marketplace add
  tedin7/subtask` (or `update`), `claude plugin install subtask@subtask`, and removes an older
  `claude mcp add` entry and skill copy made by subtask (a foreign `subtask` entry is reported,
  `--force` replaces it).
- Manual: `claude plugin marketplace add tedin7/subtask && claude plugin install subtask@subtask`.
- The plugin starts `subtask-mcp` if it is installed (fast), otherwise
  `uvx --from subtask-mcp==0.2.11 subtask-mcp` from PyPI (first start downloads it).
- The hook prints `mcp <server>/<tool> <seconds> ok|error` after every MCP tool call.
- Verify: `subtask doctor --client claude` (looks for `plugin:subtask:subtask · Connected`
  in `claude mcp list` and warns about a duplicate entry).
- Uninstall: `subtask uninstall --client claude --yes` (= `claude plugin uninstall subtask@subtask`).
- Old route, without the plugin: `subtask install --client claude --no-plugin --yes`
  (`claude mcp add` + skill copy).

## Codex

- Automatic: `subtask install --client codex --yes`
- Manual:
  ```sh
  codex mcp add subtask -- "$(uv tool dir --bin)/subtask-mcp"
  ```
  then add `tool_timeout_sec = 660` under `[mcp_servers.subtask]` in `~/.codex/config.toml`
  (default 60 s is shorter than `wait`). Skill: copy `SKILL.md` to `~/.agents/skills/subtask/`.
- Plugin: `codex plugin marketplace add /path/to/subtask` then `codex plugin add subtask@subtask`
  (same `uvx` launcher caveat as Claude).
- Verify: `codex mcp list`, `subtask doctor --client codex`
- Uninstall: `subtask uninstall --client codex --yes` or `codex mcp remove subtask`

## Kimi Code

- Automatic: `subtask install --client kimi --yes` (Kimi has no `mcp add` command; the
  installer merges one key into `~/.kimi-code/mcp.json`, or `$KIMI_CODE_HOME/mcp.json`)
- Manual: add to that file
  ```json
  {"mcpServers": {"subtask": {"command": "/abs/path/subtask-mcp", "args": [], "toolTimeoutMs": 660000}}}
  ```
  and copy `SKILL.md` to `~/.kimi-code/skills/subtask/`.
- Plugin: `plugins/kimi/subtask/kimi.plugin.json` (install only from the TUI `/plugins`).
- Verify: `subtask doctor --client kimi`; in the TUI `/mcp-config`.
- Uninstall: `subtask uninstall --client kimi --yes`

## OpenCode

- Automatic: `subtask install --client opencode --yes`
- Manual: `opencode mcp add subtask --global -- "$(uv tool dir --bin)/subtask-mcp"`;
  skill to `~/.config/opencode/skills/subtask/`.
- Optional timing log: copy `plugins/opencode/subtask-progress.ts` to
  `~/.config/opencode/plugins/` (logs `mcp subtask_<tool> <seconds>` via the app log).
- Verify: `subtask doctor --client opencode`. `opencode mcp list` starts OpenCode's managed
  service and fails if its port is taken by another running OpenCode.
- Uninstall: `subtask uninstall --client opencode --yes` (OpenCode has no `mcp remove`; the
  installer removes `mcp.servers.subtask` from `~/.config/opencode/opencode.json`).

## Troubleshooting

- `collision`: a `subtask` entry exists and runs something else. Check it, then `--force`.
- `... is not plain JSON`: the config has comments; edit it by hand with the manual step.
- `doctor` says `connects: no`: run the printed command by hand; it must be an absolute
  path to an existing `subtask-mcp`. After moving a venv, rerun `subtask install --yes`.
- `subtask-mcp not found`: pass `--command /abs/path/subtask-mcp`.
