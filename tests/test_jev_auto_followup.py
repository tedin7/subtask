"""Follow-up regressions left open by the targeted Jev hardening."""

from __future__ import annotations

import sys
import time

import pytest


JOB = "st-jev-followup"


def _terminal_job(*, review: dict | None = None):
    from subtask import jobs

    folder = jobs.job_dir(JOB)
    folder.mkdir(parents=True)
    jobs.write_json(
        folder / "spec.json",
        {"task": "Fix the parser", "engine": "fake", "max_usd": 2, "created": time.time()},
    )
    jobs.update_status(JOB, state="done", attempt=1, jev_review=review)
    (folder / "result.md").write_text("Parser fixed; review was interrupted.")
    return folder


def test_new_progress_reporter_does_not_replay_an_interrupted_jev_line(home):
    """A watch started after an interrupted review must not show stale 'in progress' text."""
    from subtask import events, progress

    review = {
        "attempt": 1,
        "state": "unknown",
        "owner": {"pid": 999999, "start": None, "boot": None},
        "created": time.time(),
        "reason": "interrupted",
    }
    _terminal_job(review=review)
    events.emit(JOB, "jev", "Jev: controllo automatico in corso")

    reporter = progress.Reporter(lambda *_: None, ids=[JOB])
    reporter.poll()
    assert "in corso" not in reporter.summary()


def test_cli_never_echoes_a_mistyped_secret_before_jev(monkeypatch, capsys):
    """The key must stay out of argparse errors even when the flag is misplaced."""
    from subtask import cli

    secret = "fake-mistyped-secret-before-jev"
    monkeypatch.setattr(sys, "argv", ["subtask", "--key", secret, "jev", "setup"])
    with pytest.raises(SystemExit):
        cli.main()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_huge_retry_timestamp_does_not_crash_wait(home):
    """An out-of-range retry timestamp is untrusted metadata, not a reason to raise."""
    from subtask import core, jobs

    folder = _terminal_job()
    jobs.write_json(
        folder / "status.json",
        {
            "v": 1,
            "state": "done",
            "attempt": 1,
            "retry_pending": True,
            "updated": 10**1000,
        },
    )
    assert isinstance(core.wait([JOB], timeout=0), str)
