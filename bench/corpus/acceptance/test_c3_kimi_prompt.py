"""Acceptance c3: Kimi 2.1 rejects --auto together with -p/--prompt; the write path must not send both."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _cli_argv import capture


def _has_prompt(argv):
    return any(a in ("-p", "--prompt") for a in argv) and "do the task" in argv


def test_write_mode_has_prompt_without_auto(monkeypatch, tmp_path):
    argv = capture(monkeypatch, tmp_path, "kimi", readonly=False)
    assert argv[0] == "kimi"
    assert _has_prompt(argv)
    assert "--auto" not in argv


def test_resume_mode_has_prompt_without_auto(monkeypatch, tmp_path):
    argv = capture(monkeypatch, tmp_path, "kimi", readonly=False, resume="sess-1")
    assert _has_prompt(argv) and "--auto" not in argv
    assert "sess-1" in argv
