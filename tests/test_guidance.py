"""Tappa 10: what a calling agent is told: MCP instructions, tool descriptions, the skill and its installer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_04 = (
    "start",
    "start_many",
    "wait",
    "integrate",
    "steer",
    "inspect",
    "map_reduce",
    "bisect",
    "consensus",
)


def _rpc(*messages: dict) -> list[dict]:
    """Talk JSON-RPC to the fast stdio server, the one clients actually launch."""
    lines = "".join(json.dumps(m) + "\n" for m in messages)
    out = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"], input=lines, capture_output=True, text=True,
        timeout=30, cwd=ROOT, check=False,
    )  # fmt: skip
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def _handshake() -> tuple[dict, list[dict]]:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}  # fmt: skip
    listed = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    replies = {
        r.get("id"): r for r in _rpc(init, {"jsonrpc": "2.0", "method": "notifications/initialized"}, listed)
    }
    return replies[1]["result"], replies[2]["result"]["tools"]


def test_instructions_reach_the_client_and_teach_every_04_tool():
    result, _ = _handshake()
    text = result.get("instructions") or ""
    assert 0 < len(text.splitlines()) < 40
    for rule in ("context_refs", "auto_repomap", "isolation='worktree'", "verify=", "invariants", "symbol_scope",
                 "action='diff'", "depends_on", "foreach", "map_reduce", "bisect", "consensus",
                 "first_success", "inspect", "steer", "oracle_cmd"):  # fmt: skip
        assert rule in text, rule


def test_tools_list_describes_every_04_parameter():
    _, tools = _handshake()
    by = {t["name"]: t for t in tools}
    assert set(TOOLS_04) <= set(by)
    for name in TOOLS_04:
        assert len(by[name]["description"]) > 60, name
        assert by[name]["inputSchema"]["type"] == "object"
    start = by["start"]
    for param in ("invariants", "allowed_paths", "symbol_scope", "oracle_cmd", "context_refs", "verify"):
        assert param in start["inputSchema"]["properties"], param
        assert param in start["description"], param
    assert "foreach" in by["start_many"]["description"] and "after" in by["start_many"]["description"]
    assert "first_success" in by["wait"]["description"]
    assert "diff" in by["integrate"]["description"] and "symbol" in by["integrate"]["description"]


def test_skill_has_frontmatter_and_recipes_for_04():
    text = (ROOT / "subtask" / "skill" / "SKILL.md").read_text()
    head = text.split("---")[1]
    assert "name: subtask" in head and "description:" in head
    for recipe in ('"symbol_scope"', '"foreach"', "oracle_cmd=", "map_reduce(", "bisect(", "consensus(",
                   'mode="first_success"', "steer(", 'action="diff"', '"context_refs"'):  # fmt: skip
        assert recipe in text, recipe
    assert (ROOT / "plugins/claude/subtask/skills/subtask/SKILL.md").read_text() == text


def test_install_skill_writes_updates_and_leaves_foreign_files(tmp_path):
    from subtask.install import commands

    dest = tmp_path / "skills"
    assert commands.install_skill(str(dest)) == [f"installed: {dest / 'subtask' / 'SKILL.md'}"]
    assert commands.install_skill(str(dest))[0].startswith("unchanged:")
    target = dest / "subtask" / "SKILL.md"
    target.write_text("---\nname: subtask\n---\nold")
    assert commands.install_skill(str(dest))[0].startswith("updated:")
    assert target.read_text() == (ROOT / "subtask" / "skill" / "SKILL.md").read_text()
    target.write_text("my own notes")
    assert commands.install_skill(str(dest))[0].startswith("skipped (not a subtask skill)")
    assert target.read_text() == "my own notes"


def test_install_skill_cli(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "install-skill", "--dest", str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT, timeout=30, check=False,
    )  # fmt: skip
    assert out.returncode == 0 and out.stdout.startswith("installed:"), out
    assert (tmp_path / "subtask" / "SKILL.md").is_file()
