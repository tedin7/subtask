"""The board: one view of groups, jobs, verify and budget, and the OpenCode plugin that renders it."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import wait_final

ROOT = Path(__file__).resolve().parents[1]
SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def _scene(core, home, monkeypatch):
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 1; " + SAY)
    base = {"engine": "fake", "cwd": str(home), "max_usd": 0.5}
    out = core.start_many([{**base, "id": "a", "task": "x", "verify": "false"},
                           {**base, "id": "b", "task": "y", "depends_on": ["a"]}])  # fmt: skip
    group = out.split()[0]
    from subtask import groups

    wait_final(groups.members(group)["jobs"][0])
    return group


def test_board_shows_the_queue_verify_and_budget(home, monkeypatch):
    from subtask import core

    group = _scene(core, home, monkeypatch)
    text = core.board()
    assert f"{group} dag" in text and "b: " in text and "← a" in text, text
    assert "verify failed (1/1)" in text and "budget: " in text
    data = core.board_data()
    assert data["groups"][0]["tasks"][1]["depends_on"] == ["a"]


def test_cli_board_json_round_trips(home, monkeypatch):
    from subtask import core

    _scene(core, home, monkeypatch)
    out = subprocess.run([sys.executable, "-m", "subtask.cli", "board", "--json"], capture_output=True,
                         text=True, check=True, cwd=ROOT)  # fmt: skip
    assert json.loads(out.stdout)["budget"]["cap_usd"] > 0


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to run the TypeScript plugin")
def test_opencode_plugin_renders_the_real_board(home, monkeypatch, tmp_path):
    from subtask import core

    group = _scene(core, home, monkeypatch)
    board = json.dumps(core.board_data())
    job = core.board_data()["jobs"][0]["id"]
    script = tmp_path / "render.ts"
    script.write_text(
        f'import {{ render }} from "{ROOT / "plugins/opencode/subtask-progress.ts"}"\n'
        f"console.log(render({board}, new Set([{json.dumps(job)}])))\n"
    )
    out = subprocess.run(["bun", str(script)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    assert f"dag {group}" in out.stdout and "← a" in out.stdout
    assert "rejoined after restart" in out.stdout and "budget:" in out.stdout


def test_board_and_plugin_show_invariants_oracle_bisect_and_fanout(home, monkeypatch, tmp_path):
    from subtask import bisect_tool, core

    board = {
        "groups": [{"id": "grp-1", "kind": "dag", "label": "consensus", "summary": "3 tasks",
                    "tasks": [{"alias": "fix[0]", "state": "started", "job": "st-a", "depends_on": [],
                               "why": "", "expanded_from": "fix"}]}],
        "jobs": [{"id": "st-a", "state": "done", "engine": "kimi", "group": None, "attempt": 1, "verify": "failed",
                  "verify_attempts": 1, "max_retries": 0, "retrying": False, "rolled_back_to": None,
                  "stalled": None, "budget_warning": None, "invariants": ["no_test_deletion: t.py has 1"],
                  "reverted": ["x.py"], "oracle": {"output_match": True, "speedup_pct": 42.0,
                                                   "ms_before": 180, "ms_after": 104}}],
        "bisects": [{"id": "bis-1", "state": "running", "round": 2, "tested": 8, "candidates": 32}],
        "budget": {"cap_usd": 50, "left_usd": 40},
    }  # fmt: skip
    script = tmp_path / "render.ts"
    script.write_text(
        f'import {{ render }} from "{ROOT / "plugins/opencode/subtask-progress.ts"}"\n'
        f"console.log(render({json.dumps(board)}, new Set()))\n"
    )
    out = subprocess.run(["bun", str(script)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    for want in ("consensus grp-1", "fix[0] (from fix)", "INVARIANT no_test_deletion", "reverted 1 file(s)",
                 "oracle same output +42% (180ms → 104ms)", "bis-1 bisect running · round 2 · 8/32 tested"):
        assert want in out.stdout, (want, out.stdout)
    bisect_tool._note("bis-2", id="bis-2", state="done", round=1, tested=4, candidates=5, commit="abcdef1234")
    assert "bis-2 bisect done · round 1 · 4/5 tested → abcdef123" in core.board()
