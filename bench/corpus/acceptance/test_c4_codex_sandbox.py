"""Acceptance c4: codex write jobs run in codex's workspace-write sandbox, never with the bypass flag."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli_argv import capture


def _pair(argv, flag, value):
    return any(argv[i] == flag and argv[i + 1] == value for i in range(len(argv) - 1))


def test_write_path_uses_workspace_write(monkeypatch, tmp_path):
    argv = capture(monkeypatch, tmp_path, "codex", readonly=False)
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert _pair(argv, "-s", "workspace-write") or _pair(argv, "--sandbox", "workspace-write")


def test_readonly_path_unchanged(monkeypatch, tmp_path):
    argv = capture(monkeypatch, tmp_path, "codex", readonly=True)
    assert _pair(argv, "-s", "read-only")
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
