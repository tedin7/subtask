"""Regression tests for the defects found by the first independent review.

No test spends API credit: API engines are only reserved/settled, and the CLI
engines are replaced by fake executables on PATH.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTASK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(tmp_path / "env"))
    monkeypatch.setenv("SUBTASK_MAX_RUNNING", "2")
    (tmp_path / "env").write_text("ANTHROPIC_API_KEY=sk-ant-test-secret-0000000000\n")
    import subtask.capacity
    import subtask.config
    import subtask.core
    import subtask.jobs

    for mod in (subtask.config, subtask.jobs, subtask.capacity, subtask.core):
        importlib.reload(mod)
    return subtask


def _fake_cli(tmp_path: Path, name: str, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / name
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"


def _wait_final(st, job_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = st.jobs.status(job_id)
        if data.get("state") in st.jobs.FINAL:
            return data
        time.sleep(0.2)
    raise AssertionError(f"{job_id} did not finish: {st.jobs.status(job_id)}")


@pytest.mark.parametrize("amount", [math.nan, -1000.0, 0.0, math.inf])
def test_bad_budget_cannot_bypass_the_cap(st, amount):
    assert st.jobs.reserve("st-aaaaaaaaaa", amount) is None
    assert st.jobs.reserve("st-bbbbbbbbbb", 1e9) is None
    assert st.jobs.month_spent() == 0


@pytest.mark.parametrize("bad", [math.nan, -5, 0, 1e6])
def test_start_rejects_bad_budget(st, tmp_path, bad):
    out = st.core.start("x", engine="kimi", cwd=str(tmp_path), max_usd=bad)
    assert out.startswith("max_usd must be")


def test_start_rejects_option_injection_in_base(st, tmp_path):
    assert st.core.start("x", engine="kimi", repo=str(tmp_path), base="--all") == "invalid base"


def test_readonly_refused_for_engines_without_a_readonly_mode(st, tmp_path):
    out = st.core.start("x", engine="kimi", cwd=str(tmp_path), readonly=True)
    assert "cannot run read-only" in out


def test_auto_never_picks_kimi_or_opencode_for_readonly(st):
    for kind in st.capacity.KINDS:
        engine, _ = st.capacity.pick(kind, 1, readonly=True)
        assert engine not in ("kimi", "opencode")


def test_child_cli_does_not_inherit_secrets(st, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak-1111111111")
    _fake_cli(tmp_path, "kimi", 'env | grep -c "^ANTHROPIC" ; exit 0')
    job = st.core.start("count", engine="kimi", cwd=str(tmp_path)).split()[0]
    _wait_final(st, job)
    assert (st.jobs.job_dir(job) / "result.md").read_text().strip() == "0"


def test_running_limit_counts_queued_jobs_in_a_burst(st, tmp_path):
    _fake_cli(tmp_path, "kimi", "sleep 5")
    outs = [st.core.start("x", engine="kimi", cwd=str(tmp_path)) for _ in range(5)]
    started = [o for o in outs if " started " in o]
    assert len(started) == 2
    for o in started:
        st.core.stop(o.split()[0])


def test_immediate_stop_is_not_lost(st, tmp_path):
    _fake_cli(tmp_path, "kimi", "sleep 30")
    job = st.core.start("x", engine="kimi", cwd=str(tmp_path)).split()[0]
    st.core.stop(job)  # possibly before the worker installs its handler
    assert _wait_final(st, job, 15)["state"] in ("stopped", "failed")
    assert st.core.reply(job, "again") != f"{job} is still running: wait or stop it"


def test_dead_worker_with_reservation_is_charged_its_budget(st):
    job = "st-cccccccccc"
    folder = st.jobs.job_dir(job)
    folder.mkdir(parents=True)
    st.jobs.write_json(folder / "spec.json", {"engine": "sonnet-api", "max_usd": 1.5, "created": time.time()})
    st.jobs.reserve(job, 1.5)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    st.jobs.write_json(folder / "status.json", {"state": "running", "pid": dead.pid})
    assert st.jobs.status(job)["state"] == "failed"
    assert st.jobs.unsettled(job) is False
    assert st.jobs.month_spent() == pytest.approx(1.5)


def test_summary_without_marker_returns_the_end(st):
    text = "\n".join(f"line {i}" for i in range(40))
    assert st.core._summary(text).splitlines()[-1] == "line 39"


def test_cleanup_keeps_uncommitted_work(st, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    _fake_cli(tmp_path, "kimi", "echo wip > wip.txt")
    job = st.core.start("x", engine="kimi", repo=str(repo)).split()[0]
    _wait_final(st, job)
    out = st.core.cleanup(job)
    assert "uncommitted" in out
    assert (Path(st.jobs.spec(job)["cwd"]) / "wip.txt").is_file()


def test_result_redacts_keys(st):
    from subtask.worker import redact

    assert "sk-ant-abcdefgh123" not in redact("key sk-ant-abcdefgh123456 here")


def test_capacity_cache_survives_zero_cap(st, monkeypatch):
    monkeypatch.setattr(st.config, "MONTHLY_CAP_USD", 0.0)
    snap = st.capacity.snapshot(refresh=True)
    assert snap["sonnet-api"]["free"] == 0.0
    assert json.loads(st.capacity.CACHE.read_text())
