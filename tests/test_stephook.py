"""Automatic step announcements from the PreToolUse hook: facts in, user-only message out."""

from __future__ import annotations

import json
import subprocess
import sys


def _transcript(tmp_path):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "rendi più veloce il conteggio dei job"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/r/subtask/admission.py"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q tests/test_x.py"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
    ]  # fmt: skip
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_transcript_gives_request_and_actions_not_tool_results(tmp_path):
    from subtask import stephook

    request, actions = stephook.read_transcript(str(_transcript(tmp_path)))
    assert request == "rendi più veloce il conteggio dei job"
    assert actions == ["Read /r/subtask/admission.py", "Bash pytest -q tests/test_x.py"]


def test_only_changes_and_commits_are_announced():
    from subtask import stephook

    assert stephook.describe_change("Read", {"file_path": "x"}) is None
    assert stephook.describe_change("Bash", {"command": "ls"}) is None
    assert "git commit" in stephook.describe_change("Bash", {"command": "git commit -m x"})
    edit = stephook.describe_change("Edit", {"file_path": "/a.py", "old_string": "a", "new_string": "b"})
    assert edit.startswith("Edit /a.py") and "- a" in edit and "+ b" in edit


def test_hook_shows_the_explanation_to_the_user_once_per_window(home, tmp_path, monkeypatch):
    from subtask import narrate, stephook

    seen = {}

    def fake_call(spec, system, user, timeout=60):
        seen["user"] = user
        return "- Hai chiesto un conteggio più veloce.\n- L'agente cambia admission.py."

    monkeypatch.setattr(narrate, "_call", fake_call)
    repo = _repo(tmp_path)
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(repo / "a.py"), "old_string": "x", "new_string": "y"},
             "transcript_path": str(_transcript(tmp_path)), "cwd": str(repo)}  # fmt: skip
    out = stephook.handle(event)
    assert out["systemMessage"].startswith("STEP · repo") and "conteggio" in out["systemMessage"]
    assert "rendi più veloce" in seen["user"] and "+ y" in seen["user"]
    assert stephook.handle(event) is None  # inside the 20-minute window: silent, no second call


def test_narrator_failure_never_blocks_and_is_logged(home, tmp_path, monkeypatch):
    from subtask import core, narrate, stephook

    monkeypatch.setattr(narrate, "_call", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    repo = _repo(tmp_path)
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(repo / "b.py"), "content": "z"},
        "cwd": str(repo),
    }
    assert stephook.handle(event) is None
    assert '"narrator": "failed"' in core.STEPS.read_text()


def test_cli_hook_exits_zero_and_prints_json_only(home, tmp_path):
    repo = _repo(tmp_path)
    event = {"tool_name": "Read", "tool_input": {"file_path": str(repo / "a.py")}, "cwd": str(repo)}
    done = subprocess.run([sys.executable, "-m", "subtask.cli", "narrate-hook"], input=json.dumps(event),
                          capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    assert done.returncode == 0 and done.stdout == ""
    bad = subprocess.run([sys.executable, "-m", "subtask.cli", "narrate-hook"], input="not json",
                         capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    assert bad.returncode == 0
