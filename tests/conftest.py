from __future__ import annotations

import importlib
import os
import stat
import time
from pathlib import Path

import pytest

MODULES = (
    "config", "jobs", "ledger", "admission", "events", "capacity", "router", "telemetry", "core", "policy",
)  # fmt: skip


@pytest.fixture(autouse=True)
def isolate_jev_configuration(tmp_path, monkeypatch):
    """The operator's automatic, paid policy must never leak into offline tests."""
    monkeypatch.setenv("SUBTASK_JEV_CONFIG", str(tmp_path / "jev.json"))
    monkeypatch.setenv("SUBTASK_JEV_KEY_FILE", str(tmp_path / "missing-jev-key"))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(tmp_path / "missing-jev-env"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private SUBTASK_HOME, fresh module state, and a bin dir first on PATH."""
    monkeypatch.setenv("SUBTASK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(tmp_path / "env"))
    monkeypatch.setenv("SUBTASK_ROUTING", str(tmp_path / "routing.json"))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-code"))
    monkeypatch.setenv("SUBTASK_MAX_RUNNING", "2")
    monkeypatch.setenv("SUBTASK_PROGRESS_MIN_S", "0")
    monkeypatch.setenv("SUBTASK_ENGINE_PLUGINS", "tests.fake_engine:adapter")
    (tmp_path / "env").write_text("ANTHROPIC_API_KEY=sk-ant-test-secret-0000000000\n")
    (tmp_path / "env").chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    for name in MODULES:
        importlib.reload(importlib.import_module(f"subtask.{name}"))
    from subtask.engines import registry

    registry.reset()
    return tmp_path


def fake_cli(tmp_path: Path, name: str, body: str) -> Path:
    exe = tmp_path / "bin" / name
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def wait_final(job_id: str, timeout: float = 30) -> dict:
    from subtask import jobs

    deadline = time.time() + timeout
    while time.time() < deadline:
        data = jobs.status(job_id)
        if data.get("state") in jobs.FINAL:
            return data
        time.sleep(0.1)
    raise AssertionError(f"{job_id} did not finish: {jobs.status(job_id)}")


def wait_until(check, timeout: float = 5):
    """Wait for an asynchronous side effect without weakening its assertions."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError("asynchronous test condition did not become true")
        time.sleep(0.01)
