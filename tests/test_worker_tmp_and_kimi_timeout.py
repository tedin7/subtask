"""Behavioral tests for per-job tmp cleanup and Kimi CLI timeout."""
from __future__ import annotations

import sys
from pathlib import Path

from subtask import runners, worker
from subtask.engines.base import Capabilities, CliAdapter, RunContext
from subtask.engines.kimi import Kimi


class _SlowAdapter(CliAdapter):
    name = "slow-test"
    family = "cli"
    default_timeout_s = 0.3
    capabilities = Capabilities(
        readonly=False,
        levels=("worktree",),
        command_network_off=False,
        cost="none",
        resume=False,
        structured_events=False,
        paid=False,
    )

    def argv(self, ctx: RunContext) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(10)"]


def test_cleanup_job_tmp_removes_only_tmp_dir(tmp_path):
    (tmp_path / "tmp" / "nested").mkdir(parents=True)
    (tmp_path / "tmp" / "nested" / "junk.json").write_text("x")
    (tmp_path / "result.md").write_text("keep")

    worker._cleanup_job_tmp(tmp_path)

    assert not (tmp_path / "tmp").exists()
    assert (tmp_path / "result.md").read_text() == "keep"


def test_cli_adapter_timeout_stops_hung_process(tmp_path):
    class _Started:
        def set(self) -> None:
            pass

    ctx = RunContext(
        job_id="st-testtimeout",
        spec={"cwd": str(tmp_path)},
        prompt="hi",
        resume=None,
        attempt=1,
        folder=tmp_path,
        emit=lambda *a, **k: None,
        started=_Started(),
        wrap=runners.direct,
        env={},
    )
    outcome = _SlowAdapter().run(ctx)
    assert outcome.ok is False
    assert outcome.subtype == "timeout"
    assert "timed out" in outcome.result
    assert Kimi.default_timeout_s == 1500.0
