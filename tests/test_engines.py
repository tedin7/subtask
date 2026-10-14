"""R07, R14: adapter contracts on real recorded output, probing, and a plugin engine."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from .conftest import fake_cli, wait_final

FIX = Path(__file__).parent / "fixtures" / "engines"


def _parse_all(adapter, name: str) -> dict:
    state: dict = {"text": [], "commands": [], "session": None, "tokens": {}, "cost": None}
    activity = []
    for line in (FIX / name).read_text().splitlines():
        out = adapter.parse(line, state)
        if out:
            activity.append(out)
    state["activity"] = activity
    return state


@pytest.mark.contract
def test_kimi_contract_on_recorded_2_1_1_output():
    from subtask.engines.kimi import Kimi

    state = _parse_all(Kimi(), "kimi-2.1.1.jsonl")
    assert state["session"].startswith("session_")
    assert state["text"] == ["OK"]
    assert state["activity"] == ["running: echo hi"]
    assert state["commands"] == [{"command": "echo hi", "exit_code": None}]


def test_kimi_argv_never_combines_prompt_with_auto():
    from subtask.engines.base import RunContext
    from subtask.engines.kimi import Kimi

    ctx = RunContext("st-x", {"cwd": "/tmp"}, "hi", None, 1, Path("/tmp"), print, threading.Event(), None, {})
    argv = Kimi().argv(ctx)
    assert "--auto" not in argv and argv[-2:] == ["-p", "hi"]


@pytest.mark.contract
def test_opencode_contract_on_recorded_2_0_28_output():
    from subtask.engines.opencode import OpenCodeGo

    state = _parse_all(OpenCodeGo(), "opencode-2.0.28.jsonl")
    assert state["session"].startswith("ses_")
    assert state["text"] == ["OK"]
    assert state["commands"][0]["command"] == "echo hi"
    assert state["tokens"]["input"] > 0
    assert state["cost_reported"] > 0


def test_opencode_quota_error_is_recognised():
    from subtask.engines.opencode import OpenCode

    state = _parse_all(OpenCode(), "opencode-2.0.28-quota.jsonl")
    assert state["quota"] and "credits" in state["error"]


def test_codex_usage_limit_is_recognised_with_reset_time():
    from subtask.engines.codex import Codex

    state = _parse_all(Codex(), "codex-0.162.1-usage-limit.jsonl")
    assert state["quota"] and state["error_kind"] == "quota exhausted"
    assert state["resets_at"] and state["resets_at"] > 1.7e9
    assert state["session"]


def test_codex_command_events_documented_format():
    """Format from the Codex `exec --json` docs (no live sample: account at usage limit)."""
    from subtask.engines.codex import Codex

    lines = [
        {"type": "item.started", "item": {"type": "command_execution", "command": "bash -lc 'pytest -q'"}},
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "bash -lc 'pytest -q'", "exit_code": 1},
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "## Summary\nfailed"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]
    state = {"text": [], "commands": [], "session": None, "tokens": {}, "cost": None}
    acts = [Codex().parse(json.dumps(x), state) for x in lines]
    assert acts[0] == "running tests: pytest -q"
    assert state["commands"] == [{"command": "pytest -q", "exit_code": 1}]
    assert state["tokens"] == {"input_tokens": 10, "output_tokens": 5}


def test_codex_never_bypasses_its_sandbox_unless_asked():
    from subtask.engines.base import RunContext
    from subtask.engines.codex import Codex

    def argv(level, readonly=False):
        spec = {"cwd": "/w", "git_dir": "/r/.git", "readonly": readonly, "policy": {"level": level}}
        ctx = RunContext("st-x", spec, "p", None, 1, Path("/tmp"), print, threading.Event(), None, {})
        return Codex().argv(ctx)

    assert "--dangerously-bypass-approvals-and-sandbox" not in argv("sandbox")
    assert ["-s", "workspace-write"] == argv("sandbox")[7:9] or "workspace-write" in argv("sandbox")
    assert "/r/.git" in argv("sandbox")
    assert "read-only" in argv("worktree", readonly=True)
    assert "--dangerously-bypass-approvals-and-sandbox" in argv("worktree")


def test_probe_reports_missing_binary_and_flags(home):
    from subtask.engines.kimi import Kimi

    assert Kimi().probe().installed in (True, False)
    fake_cli(home, "kimi", 'case "$1" in --version) echo 9.9;; *) echo "--prompt --session";; esac')
    probe = Kimi().probe()
    assert probe.installed and probe.version == "9.9"
    assert probe.flags["--output-format"] is False and not probe.compatible
    assert any("--output-format" in p for p in probe.problems)


def test_plugin_engine_runs_without_core_changes(home, monkeypatch):
    from subtask import core, jobs, ledger
    from subtask.engines import registry

    assert "fake" in registry.names()
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        'echo \'{"tool":"pytest -q","exit":0}\'; echo \'{"say":"all good"}\'',
    )
    job = core.start("x", engine="fake", cwd=str(home), max_usd=1).split()[0]
    final = wait_final(job)
    assert final["state"] == "done"
    assert final["cost_usd"] == pytest.approx(0.25)
    assert ledger.committed() == pytest.approx(0.25)
    attempt = jobs.status(job)["attempts"][-1]
    assert attempt["commands"] == [{"command": "pytest -q", "exit_code": 0}]


def test_broken_plugin_is_reported_not_fatal(home, monkeypatch):
    monkeypatch.setenv("SUBTASK_ENGINE_PLUGINS", "tests.fake_engine:adapter,nope.module:x")
    from subtask.engines import registry

    registry.reset()
    assert "fake" in registry.names() and "kimi" in registry.names()
    assert any("nope.module" in e for e in registry.plugin_errors())


def test_unknown_output_format_is_counted_not_crashing(home):
    from subtask import core

    fake_cli(home, "kimi", 'echo \'{"role":"assistant","tool_calls":"not-a-list"}\'; echo plain answer')
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    assert wait_final(job)["state"] == "done"
