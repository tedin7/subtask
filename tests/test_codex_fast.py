"""Tests for fast Codex execution (--fast / fast=True / model_reasoning_effort + service_tier)."""

from __future__ import annotations

import threading
from pathlib import Path

from subtask import cli, server
from subtask.engines.base import RunContext
from subtask.engines.codex import Codex

from .conftest import wait_final


def _codex_argv(effort: str | None = "medium", fast: bool = False) -> list[str]:
    spec: dict = {"cwd": "/w", "policy": {"level": "sandbox"}}
    if effort is not None:
        spec["effort"] = effort
    if fast:
        spec["fast"] = True
    ctx = RunContext("st-x", spec, "p", None, 1, Path("/tmp"), print, threading.Event(), None, {})
    return Codex().argv(ctx)


def test_codex_argv_sets_reasoning_effort_and_ultrafast_tier():
    low = _codex_argv(effort="low")
    assert 'model_reasoning_effort="low"' in low
    assert 'service_tier="ultrafast"' in low

    medium = _codex_argv(effort="medium")
    assert 'model_reasoning_effort="medium"' in medium
    assert not any("service_tier" in part for part in medium)

    high = _codex_argv(effort="high")
    assert 'model_reasoning_effort="high"' in high
    assert not any("service_tier" in part for part in high)

    fast_medium = _codex_argv(effort="medium", fast=True)
    assert 'model_reasoning_effort="medium"' in fast_medium
    assert 'service_tier="ultrafast"' in fast_medium


def test_cli_start_parses_fast_and_tier_flags(monkeypatch, capsys):
    calls: list[dict] = []

    def fake_start(task, *args, **kwargs):
        calls.append({"task": task, "args": args, "kwargs": kwargs})
        return "st-test started"

    monkeypatch.setattr(cli.core, "start", fake_start)
    monkeypatch.setattr("sys.argv", ["subtask", "start", "hello", "--fast", "--tier", "fast"])
    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == "st-test started"
    assert len(calls) == 1
    assert calls[0]["kwargs"]["fast"] is True
    assert calls[0]["kwargs"]["tier"] == "fast"


def test_core_and_server_start_fast_sets_tier_effort_and_spec(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setattr(server, "core", core)
    job_core = core.start("x", engine="fake", cwd=str(home), fast=True).split()[0]
    wait_final(job_core)
    spec_core = jobs.spec(job_core)
    assert spec_core["fast"] is True
    assert spec_core["tier"] == "fast"
    assert spec_core["effort"] == "low"

    job_mcp = server.start("y", engine="fake", cwd=str(home), fast=True).split()[0]
    wait_final(job_mcp)
    spec_mcp = jobs.spec(job_mcp)
    assert spec_mcp["fast"] is True
    assert spec_mcp["tier"] == "fast"
    assert spec_mcp["effort"] == "low"
