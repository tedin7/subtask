"""Behaviours of the live compaction bench that do not need a model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import context_compaction as cc  # noqa: E402
import context_compaction_live as live  # noqa: E402


def test_last_line_is_last_non_empty_line_with_spaces_collapsed():
    assert live.last_line("a\n  b   c  \n\n") == "b c"


def test_state_pointer_names_the_local_file_so_the_model_can_read_it():
    calls = [{"line": 7, "tool": "bash", "cmd": "ls x", "out": "ls: cannot access x", "ok": False}]
    state = live.context_for("state", calls, "st-1", "st-1.jsonl")
    assert "st-1.jsonl:7" in state and "job:" not in state
    assert "ls: cannot access x" not in state  # the proof itself is not carried, only its pointer


def test_summary_drops_a_proof_in_the_middle_full_keeps_it():
    calls = [{"line": i, "tool": "bash", "cmd": f"c{i}", "out": "ok", "ok": True} for i in range(40)]
    calls[20] = {"line": 20, "tool": "bash", "cmd": "c20", "out": "Traceback boom", "ok": False}
    assert "boom" in live.context_for("full", calls, "j", "j.jsonl")
    assert "boom" not in live.context_for("summary", calls, "j", "j.jsonl")
    assert cc.is_proof(calls[20])
