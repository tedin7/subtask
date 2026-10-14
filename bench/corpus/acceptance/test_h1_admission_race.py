"""Acceptance h1: MAX_RUNNING holds across processes (no check-then-act race in start())."""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHILD = r"""
import os, subprocess, sys, time
from subtask import core
def spawn(job_id, *extra):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)", "subtask.worker", job_id],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.pid
core._spawn = spawn
go = float(sys.argv[1])
while time.time() < go:
    pass
print(core.start("x", engine="codex", cwd=os.environ["SUBTASK_HOME"]).splitlines()[0])
"""


def _trial(tmp: Path, n: int = 12, cap: int = 2) -> int:
    env = {**os.environ, "SUBTASK_HOME": str(tmp), "SUBTASK_MAX_RUNNING": str(cap), "PYTHONPATH": str(ROOT)}
    go = time.time() + 2.0
    procs = [
        subprocess.Popen([sys.executable, "-c", CHILD, str(go)], env=env, cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
        for _ in range(n)
    ]  # fmt: skip
    outs = [p.communicate(timeout=60)[0] for p in procs]
    return sum(1 for o in outs if " started " in o)


def test_max_running_holds_across_processes(tmp_path):
    for i in range(3):
        home = tmp_path / f"t{i}"
        home.mkdir()
        started = _trial(home)
        assert 1 <= started <= 2, f"trial {i}: {started} jobs admitted with SUBTASK_MAX_RUNNING=2"
