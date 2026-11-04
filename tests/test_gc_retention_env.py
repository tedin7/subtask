"""`subtask gc` without --days follows SUBTASK_JOB_RETENTION_DAYS, as the README says (T-7465)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import subtask as _package

SOURCE = str(Path(_package.__file__).parents[1])


def _preview(*args, retention=None):
    env = {k: v for k, v in os.environ.items() if k != "SUBTASK_JOB_RETENTION_DAYS"}
    env["PYTHONPATH"] = SOURCE
    if retention is not None:
        env["SUBTASK_JOB_RETENTION_DAYS"] = retention
    done = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "gc", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return done.stdout


def _old_job(home, age_days):
    from subtask import jobs

    folder = jobs.job_dir("st-old")
    folder.mkdir(parents=True)
    jobs.write_json(
        folder / "status.json", {"v": 1, "state": "done", "updated": time.time() - age_days * 86400}
    )
    jobs.write_json(folder / "spec.json", {})


def test_cli_default_follows_the_env_threshold(home):
    _old_job(home, 20)
    assert "would remove 1 job folders" in _preview(retention="14")


def test_cli_default_stays_30_days_without_env(home):
    _old_job(home, 20)
    assert "would remove 0 job folders" in _preview()


def test_explicit_days_win_over_env(home):
    _old_job(home, 20)
    assert "would remove 0 job folders" in _preview("--days", "30", retention="14")
