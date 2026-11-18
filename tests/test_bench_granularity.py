"""The setup-recovery and context benches keep the guarantees their comparison reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import context_compaction as cc
import install_granularity as ig


def _row(strategy: str, fault: int) -> dict:
    return ig.scenario(strategy, ig.FAULTS[fault])


def test_program_rollback_leaves_no_partial_change_and_recovers() -> None:
    row = _row("program", 2)  # the last operation fails after three succeeded
    assert row["files_left_changed_after_fail"] == 0
    assert row["failed_unit_restored_byte_identical"] is True
    assert row["recovered"] is True


def test_group_rollback_keeps_finished_groups_and_redoes_only_the_failed_one() -> None:
    row = _row("group", 2)
    assert row["files_left_changed_after_fail"] > 0  # the first client stays installed
    assert row["failed_unit_restored_byte_identical"] is True
    assert row["retry_ops"] < _row("program", 2)["retry_ops"]
    assert row["recovered"] is True


def test_context_state_pointer_returns_the_exact_failing_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cc, "JOBS", tmp_path)
    job = tmp_path / "st-aaaaaaaaaa"
    job.mkdir()
    long_fail = "Traceback (most recent call last)\n" + "x" * 5000
    events = [
        '{"type":"item.completed","item":{"type":"command_execution","command":"ls","aggregated_output":"fine","exit_code":0}}'
    ] * 30
    events.insert(
        10,
        '{"type":"item.completed","item":{"type":"command_execution","command":"pytest","aggregated_output":'
        + json.dumps(long_fail)
        + ',"exit_code":1}}',
    )
    (job / "output-1.jsonl").write_text("\n".join(events) + "\n")
    row = cc.measure("st-aaaaaaaaaa")
    assert row["proofs"] == 1
    assert row["state"]["recovered"] == 1  # followed the pointer, hash matches
    assert row["summary"]["recovered"] == 0  # the clipped summary lost the exact text
    assert row["state"]["tokens"] < row["full"]["tokens"]
