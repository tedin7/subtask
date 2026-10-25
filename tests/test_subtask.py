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
    from subtask import policy, router

    for kind in router.KINDS:
        engine, _, _ = router.pick(kind, 1, policy.demand(readonly=True))
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
    assert snap["sonnet-api"]["value"] == 0.0
    assert json.loads(st.capacity._cache().read_text())


def test_dead_worker_is_charged_once_under_concurrent_status(st):
    import threading

    job = "st-dddddddddd"
    folder = st.jobs.job_dir(job)
    folder.mkdir(parents=True)
    st.jobs.write_json(folder / "spec.json", {"engine": "sonnet-api", "max_usd": 2.0, "created": time.time()})
    st.jobs.reserve(job, 2.0)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    st.jobs.write_json(folder / "status.json", {"state": "running", "pid": dead.pid})
    threads = [threading.Thread(target=st.jobs.status, args=(job,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert st.jobs.month_spent() == pytest.approx(2.0)


def test_engine_that_never_started_costs_nothing(st, tmp_path, monkeypatch):
    (tmp_path / "env").write_text("")  # no key: the api engine fails before any call
    job = st.core.start("x", engine="sonnet-api", cwd=str(tmp_path), max_usd=2).split()[0]
    assert _wait_final(st, job)["state"] == "failed"
    assert st.jobs.month_spent() == 0


def test_secrets_are_scrubbed_from_the_worker_environment(st, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("SUBTASK_ANTHROPIC_API_KEY", "sk-ant-x-1234567890")
    _fake_cli(tmp_path, "kimi", 'env | grep -c -E "GITHUB_TOKEN|ANTHROPIC" ; exit 0')
    job = st.core.start("count", engine="kimi", cwd=str(tmp_path)).split()[0]
    _wait_final(st, job)
    assert (st.jobs.job_dir(job) / "result.md").read_text().strip() == "0"


def test_wait_on_unknown_id_returns_at_once(st):
    t = time.time()
    assert st.core.wait(["st-0000000000"], 30).startswith("unknown job")
    assert time.time() - t < 1


def test_failed_reply_keeps_the_previous_result(st, tmp_path):
    _fake_cli(tmp_path, "kimi", "echo first answer")
    job = st.core.start("x", engine="kimi", cwd=str(tmp_path)).split()[0]
    _wait_final(st, job)
    _fake_cli(tmp_path, "kimi", "echo broken; exit 3")
    st.core.reply(job, "again")
    time.sleep(0.5)
    _wait_final(st, job)
    assert "first answer" in (st.jobs.job_dir(job) / "result.md").read_text()


def test_start_many_reports_bad_items_and_keeps_started_ids(st, tmp_path):
    _fake_cli(tmp_path, "kimi", "true")
    out = st.core.start_many(
        [
            {"task": "a", "engine": "kimi", "cwd": str(tmp_path)},
            {"task": "b", "engine": "kimi", "max_turns": "abc"},
        ]
    )
    assert " started " in out.splitlines()[0]
    assert "max_turns" in out.splitlines()[1]


def test_bad_cap_env_falls_back(monkeypatch):
    monkeypatch.setenv("SUBTASK_MONTHLY_CAP_USD", "nan")
    import subtask.config

    assert importlib.reload(subtask.config).MONTHLY_CAP_USD == 180


def test_dsh_cost_counts_uncached_cached_and_output_tokens(st):
    from subtask import worker

    usage = {"inputTokens": 1_000_000, "cacheReadTokens": 1_000_000, "outputTokens": 1_000_000}
    events = [{"type": "assistant/message", "data": {"usage": usage}}, {"type": "turn/end", "data": {}}]
    assert math.isclose(worker.dsh_cost(events), 0.30 + 0.006 + 1.20)


def test_dsh_refused_read_only_and_skipped_by_auto(st, tmp_path):
    out = st.core.start("x", "dsh", None, str(tmp_path), "HEAD", True, None, 5, "low")
    assert "cannot run read-only" in out
    from subtask import policy, router

    engine, _, _ = router.pick("read", None, policy.demand(readonly=True))
    assert engine not in ("dsh", "kimi", "opencode", "opencode-go")


def test_dsh_key_never_taken_from_environment(st, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    monkeypatch.setenv("SUBTASK_DSH_KEY_FILE", str(tmp_path / "missing"))
    importlib.reload(st.config)
    assert st.config.deepseek_key() == ""


def test_fast_server_publishes_what_fastmcp_would():
    import asyncio

    from subtask import fastserver, server

    tools = asyncio.run(server.mcp.list_tools())
    published = [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]
    assert fastserver.SPEC["tools"] == published
    assert fastserver.SPEC["instructions"] == server.mcp.instructions


def test_fast_server_speaks_json_rpc(st, tmp_path):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "bogus"},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"],
        input="".join(json.dumps(m) + "\n" for m in msgs) + "not json\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ},
    )
    out = {r.get("id"): r for r in map(json.loads, proc.stdout.splitlines())}
    assert out[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(out[2]["result"]["tools"]) == 17
    assert out[4]["result"]["isError"] is True
    assert out[5]["error"]["code"] == -32601
    assert out[None]["error"]["code"] == -32700
