"""Live adapter checks: one tiny real job per engine, through subtask itself.

Marked `live`: skipped by default (pytest.ini addopts "-m 'not live'"). Run with
`pytest -m live` when spending is authorized. Each job is capped at 0.20 USD
(paid engines) and asks for one shell command and a one-word answer, so the
contract with the real CLI/SDK is exercised: argv accepted, events parsed,
session id found, result written, cost booked.
Engines that are not installed or not logged in are skipped, never faked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

TASK = "Run the shell command `echo subtask-live-ok` once, then answer with exactly that output."
ENGINES = ["haiku-api", "opencode-go", "kimi", "codex"]


def _cli(home: Path, *args: str, timeout: float = 300) -> str:
    env = {**os.environ, "SUBTASK_HOME": str(home), "SUBTASK_MAX_RUNNING": "4"}
    done = subprocess.run([sys.executable, "-m", "subtask.cli", *args], env=env, capture_output=True,
                          text=True, timeout=timeout, check=False)  # fmt: skip
    return done.stdout + done.stderr


def _available(engine: str) -> str | None:
    from subtask import config
    from subtask.engines import registry

    adapter = registry.get(engine)
    if adapter is None or not adapter.available():
        return f"{engine} not installed"
    if (
        adapter.capabilities.paid
        and engine.endswith("-api")
        and not config.load_secrets().get("ANTHROPIC_API_KEY")
    ):
        return "no ANTHROPIC_API_KEY in the subtask env file"
    cap = adapter.capacity()
    if cap.value is not None and cap.value < 10:
        return f"{engine} has no quota left ({cap.note})"
    return None


@pytest.mark.parametrize("engine", ENGINES)
def test_engine_runs_a_tiny_real_job(engine, tmp_path):
    reason = _available(engine)
    if reason:
        pytest.skip(reason)
    if shutil.which("git") is None:
        pytest.skip("git missing")
    work = tmp_path / "work"
    work.mkdir()
    out = _cli(tmp_path / "home", "start", TASK, "--engine", engine, "--cwd", str(work), "--max-usd", "0.2",
               "--max-turns", "6", "--effort", "low")  # fmt: skip
    assert " started " in out, out
    job = out.split()[0]
    t0 = time.time()
    final = _cli(tmp_path / "home", "wait", job, "--timeout", "280")
    assert " done " in final, final
    env = json.loads(_cli(tmp_path / "home", "result", job, "--evidence"))
    assert "subtask-live-ok" in env["summary"]["text"], env["summary"]
    assert env["commands"]["seen"] >= 1, "no shell command observed: event format changed?"
    if env["cost"]["reserved"] is not None:
        assert 0 <= (env["cost"]["charged_total"] or 0) <= 0.2
    assert time.time() - t0 < 300
