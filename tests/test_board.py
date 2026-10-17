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
