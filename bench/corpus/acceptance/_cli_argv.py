"""Shared helper for c3/c4: capture the argv worker._run_cli builds, without running anything."""

import subprocess

from subtask import config, worker

JOB = "st-aaaaaaaaaa"


def capture(monkeypatch, tmp_path, engine: str, readonly: bool, resume=None) -> list[str]:
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    (tmp_path / "jobs" / JOB).mkdir(parents=True)
    seen: dict = {}

    def fake_run(argv, **_kw):
        seen["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    worker._run_cli(
        JOB, {"engine": engine, "cwd": str(tmp_path), "readonly": readonly}, "do the task", resume
    )
    return seen["argv"]
