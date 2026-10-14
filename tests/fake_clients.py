#!/usr/bin/env python3
"""Stand-in for the claude / codex / opencode / kimi CLIs: logs argv, edits config like the real one."""

import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
home = Path(os.environ["HOME"])
with open(home / "argv.log", "a") as log:
    log.write(json.dumps([name, *args]) + "\n")
if args == ["--version"]:
    print(f"{name} 9.9.9 (fake)")
    sys.exit(0)
if os.environ.get("FAKE_FAIL") == name:
    print("simulated failure", file=sys.stderr)
    sys.exit(3)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


cmd = args[args.index("--") + 1 :] if "--" in args else []
if name == "claude":
    path = home / ".claude.json"
    data = load(path)
    servers = data.setdefault("mcpServers", {})
    if args[:2] == ["mcp", "add"]:
        if "subtask" in servers:
            sys.exit("already exists")
        servers["subtask"] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {}}
    elif args[:2] == ["mcp", "remove"]:
        servers.pop("subtask", None)
    save(path, data)
elif name == "codex":
    path = Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml"
    text = path.read_text() if path.exists() else ""
    if args[:2] == ["mcp", "add"] and os.environ.get("FAKE_NO_TABLE") != "1":
        block = f"[mcp_servers.subtask]\ncommand = {json.dumps(cmd[0])}\n"
        if cmd[1:]:
            block += f"args = {json.dumps(cmd[1:])}\n"
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    elif args[:2] == ["mcp", "remove"]:
        start = text.index("[mcp_servers.subtask]")
        nxt = text.find("\n[", start + 1)
        text = text[:start].rstrip("\n") + ("\n" + text[nxt:] if nxt != -1 else "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
elif name == "opencode" and args[:2] == ["mcp", "add"]:
    base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode" / "opencode.json"
    data = load(base)
    data.setdefault("mcp", {}).setdefault("servers", {})["subtask"] = {"type": "local", "command": cmd}
    save(base, data)
