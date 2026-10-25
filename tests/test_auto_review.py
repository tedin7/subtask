"""Automatic reviews use the real review contract, with an offline provider double."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from .test_delivery_review import answer


@pytest.fixture
def delivery(home, monkeypatch):
    from subtask import auto_review, jev_config, jobs

    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-jev-auto-secret-for-tests")
    jev_config.configure(mode="auto")
    folder = jobs.job_dir("st-auto-review")
    folder.mkdir(parents=True)
    jobs.write_json(
        folder / "spec.json",
        {
            "task": "Fix the parser",
            "engine": "fake",
            "max_usd": 2,
            "created": time.time(),
        },
    )
    jobs.update_status(
        "st-auto-review",
        state="done",
        attempt=1,
        session_id="test-session",
        jev_review=auto_review.prepare(1, "done", False, []),
    )
    (folder / "result.md").write_text("Parser fixed; verification was not run.")
    return folder


def test_delivery_starts_one_review_and_wait_includes_the_advice(delivery, monkeypatch):
    from subtask import auto_review, core, delivery_review, jobs, ledger

    calls = []

    def provider(packet, key):
        calls.append(packet)
        return answer(packet, "supported")

    monkeypatch.setattr(delivery_review, "_request", provider)
    assert "still running" in core.wait(["st-auto-review"], timeout=0)
    assert "Jev: in corso" in core.result("st-auto-review")
    auto_review.perform("st-auto-review", 1)
    auto_review.perform("st-auto-review", 1)
    out = core.wait(["st-auto-review"], timeout=0)
    assert "st-auto-review done" in out and "Jev:" in out
    assert len(calls) == 1
    status = jobs.status("st-auto-review")
    assert status["state"] == "done" and status["attempt"] == 1
    assert status["jev_review"]["state"] == "reviewed"
    report = status["jev_review"]["report"]
    assert report["scope_assessment"]["assessment"] == "unproven"
    assert all(a["assessment"] == "unproven" for a in report["assessments"])
    assert ledger.committed() == 2 and ledger.open_for("st-auto-review") == []
    assert next(r for r in ledger._rows(ledger.month_of()) if r["op"] == "reserve")["engine"] == "jev"
    assert json.loads(core.result("st-auto-review", evidence=True))["jev_review"]["advisory_only"]


def test_concurrent_claims_cannot_submit_twice(delivery, monkeypatch):
    from subtask import auto_review, delivery_review, ledger

    calls = []
    entered, release = threading.Event(), threading.Event()

    def provider(packet, key):
        calls.append(packet)
        entered.set()
        assert release.wait(5)
        return answer(packet)

    monkeypatch.setattr(delivery_review, "_request", provider)
    first = threading.Thread(target=auto_review.perform, args=("st-auto-review", 1))
    first.start()
    try:
        assert entered.wait(5)
        auto_review.perform("st-auto-review", 1)
    finally:
        release.set()
        first.join(5)
    assert not first.is_alive() and len(calls) == 1 and ledger.committed() == 2


@pytest.mark.parametrize("block", ["key", "budget"])
def test_missing_key_and_budget_do_not_call_provider(delivery, monkeypatch, block):
    from subtask import auto_review, config, delivery_review, jobs, ledger

    if block == "key":
        monkeypatch.delenv("TYPESAFE_API_KEY")
    else:
        monkeypatch.setattr(config, "MONTHLY_CAP_USD", 1)
    monkeypatch.setattr(delivery_review, "_request", lambda *_: pytest.fail("must not submit"))
    auto_review.perform("st-auto-review", 1)
    assert jobs.status("st-auto-review")["jev_review"]["state"] == "blocked"
    assert ledger.committed() == 0


def test_provider_failure_finishes_the_wait_without_resubmission(delivery, monkeypatch):
    from subtask import auto_review, core, delivery_review, jobs, ledger

    calls = []

    def unavailable(*args):
        calls.append(1)
        raise TimeoutError("offline timeout")

    monkeypatch.setattr(delivery_review, "_request", unavailable)
    auto_review.perform("st-auto-review", 1)
    auto_review.perform("st-auto-review", 1)
    assert len(calls) == 1
    assert "st-auto-review done" in core.wait(["st-auto-review"], timeout=0)
    assert jobs.status("st-auto-review")["jev_review"]["state"] == "unknown"
    assert ledger.committed() == 2


def test_disabling_before_submission_preserves_the_delivery(delivery, monkeypatch):
    from subtask import auto_review, delivery_review, jev_config, jobs, ledger

    jev_config.configure(mode="off")
    monkeypatch.setattr(delivery_review, "_request", lambda *_: pytest.fail("disabled"))
    auto_review.perform("st-auto-review", 1)
    status = jobs.status("st-auto-review")
    assert status["state"] == "done" and status["jev_review"]["state"] == "skipped"
    assert ledger.committed() == 0


def test_review_does_not_turn_a_failed_delivery_into_a_success(delivery, monkeypatch):
    from subtask import auto_review, delivery_review, jobs

    jobs.update_status("st-auto-review", state="failed")
    monkeypatch.setattr(delivery_review, "_request", lambda packet, key: answer(packet))
    auto_review.perform("st-auto-review", 1)
    status = jobs.status("st-auto-review")
    assert status["state"] == "failed" and status["jev_review"]["state"] == "reviewed"


def test_reply_is_deferred_and_hints_are_preserved_while_reviewing(delivery):
    from subtask import core, jobs

    assert "Jev review is still running" in core.reply("st-auto-review", "more")
    assert "hint queued" in core.steer("st-auto-review", "hint", "keep this instruction")
    assert core._deliver_hints(["st-auto-review"]) == []
    assert jobs.status("st-auto-review")["hints"] == ["keep this instruction"]


def test_progress_does_not_count_a_pending_review_as_an_ended_delivery(delivery):
    from subtask import progress

    reporter = progress.Reporter(lambda *_: None, ids=["st-auto-review"])
    reporter.poll()
    assert reporter.states["st-auto-review"] == "jev"
    assert "Jev:" in reporter.summary() and " done " not in reporter.summary()


@pytest.mark.parametrize("created", ["bad", float("nan"), float("inf"), time.time() + 1000])
def test_invalid_pending_metadata_cannot_hold_wait_forever(delivery, created):
    from subtask import auto_review, jobs

    status = jobs.status("st-auto-review")
    jobs.update_status("st-auto-review", jev_review={**status["jev_review"], "created": created})
    assert not auto_review.pending("st-auto-review", jobs.status("st-auto-review"))
    assert jobs.status("st-auto-review")["jev_review"]["state"] == "unknown"


@pytest.mark.parametrize("new_state", ["reviewing", "reviewed"])
def test_stale_observer_cannot_overwrite_a_newer_review_state(delivery, monkeypatch, new_state):
    from subtask import auto_review, jobs, procid

    snapshot = jobs.status("st-auto-review")

    def changed_owner(owner):
        jobs.update_status("st-auto-review", jev_review={**snapshot["jev_review"], "state": new_state})
        return procid.DEAD

    monkeypatch.setattr(procid, "alive", changed_owner)
    assert auto_review.pending("st-auto-review", snapshot) == (new_state == "reviewing")
    assert jobs.status("st-auto-review")["jev_review"]["state"] == new_state


def test_child_deadline_survives_the_supervisor(home):
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; from subtask import auto_review as a; a.TIMEOUT_S=.1; "
                "a.perform=lambda *_: time.sleep(30); sys.argv=['auto','st-deadline','1']; a.main()"
            ),
        ]
    )
    try:
        assert child.wait(timeout=5) == 124
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_supervisor_timeout_kills_only_its_owned_child(delivery, monkeypatch):
    from subtask import auto_review, jobs

    real_popen = subprocess.Popen
    sentinel = real_popen([sys.executable, "-c", "import time; time.sleep(30)"])
    owned = []

    def spawn(*args, **kwargs):
        proc = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        owned.append(proc)
        return proc

    monkeypatch.setattr(auto_review.subprocess, "Popen", spawn)
    monkeypatch.setattr(auto_review, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(auto_review, "SUPERVISOR_GRACE_S", 0.05)
    try:
        auto_review.finish("st-auto-review", 1)
        assert len(owned) == 1 and owned[0].poll() is not None
        assert sentinel.poll() is None
        assert jobs.status("st-auto-review")["jev_review"]["state"] == "unknown"
    finally:
        sentinel.kill()
        sentinel.wait()


def test_supervisor_recovers_a_completed_receipt_without_sending_again(delivery, monkeypatch):
    from subtask import auto_review, delivery_review, jobs, ledger

    calls = []

    def provider(packet, key):
        calls.append(1)
        return answer(packet)

    monkeypatch.setattr(delivery_review, "_request", provider)
    delivery_review.review("st-auto-review", auto_review.CRITERIA, submit=True)
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        auto_review.subprocess, "Popen", lambda *args, **kw: real_popen([sys.executable, "-c", "pass"], **kw)
    )
    auto_review.finish("st-auto-review", 1)
    assert calls == [1] and ledger.committed() == 2
    assert jobs.status("st-auto-review")["jev_review"]["state"] == "reviewed"


def test_environment_credential_uses_a_pipe_never_a_process_argument(delivery, monkeypatch):
    from subtask import auto_review, delivery_review

    key = "fake-pipe-only-jev-credential"
    received = delivery / "fake-pipe-input"
    real_popen = subprocess.Popen

    def spawn(args, **kw):
        assert key not in " ".join(args)
        assert kw["stdin"] == subprocess.PIPE
        return real_popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from pathlib import Path; "
                    "Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
                ),
                str(received),
            ],
            **kw,
        )

    monkeypatch.setattr(auto_review.subprocess, "Popen", spawn)
    monkeypatch.setattr(delivery_review, "_request", lambda *_: pytest.fail("offline recovery only"))
    auto_review.finish("st-auto-review", 1, api_key=key)
    assert received.read_text() == key


def test_reply_after_a_review_process_dies_does_not_deadlock(delivery, monkeypatch):
    from subtask import core, jobs, procid

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    owner = procid.identity(child.pid)
    child.wait(timeout=5)
    status = jobs.status("st-auto-review")
    jobs.update_status("st-auto-review", jev_review={**status["jev_review"], "owner": owner})
    monkeypatch.setattr(core, "_spawn", lambda *_: procid.identity())
    replies = []
    thread = threading.Thread(
        target=lambda: replies.append(core.reply("st-auto-review", "continue")), daemon=True
    )
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and replies[0].endswith("resumed")


@pytest.mark.parametrize("source", ["managed", "env"])
def test_worker_delivery_triggers_review_without_a_parent_review_call(home, monkeypatch, source):
    from subtask import auto_review, config, core, delivery_review, jev_config, jobs, procid, worker

    key = "fake-managed-jev-key-for-worker-test"
    if source == "managed":
        jev_config.configure(mode="auto", api_key=key)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
        jev_config.configure(mode="auto")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", ('test -z "$TYPESAFE_API_KEY" || exit 99; echo \'{"say":"parser fixed"}\'')
    )
    monkeypatch.setattr(config, "DSH_KEY_FILE", home / "missing-deepseek")
    monkeypatch.setattr(core, "_spawn", lambda *_: procid.identity())
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    calls = []

    def provider(packet, key):
        calls.append(packet)
        return answer(packet)

    monkeypatch.setattr(delivery_review, "_request", provider)

    def finish_inline(job, attempt, *, api_key=None):
        extra = {"TYPESAFE_API_KEY": api_key} if api_key else {}
        with patch.dict(os.environ, extra):
            auto_review.perform(job, attempt)

    monkeypatch.setattr(auto_review, "finish", finish_inline)
    out = core.start("Fix the parser", engine="fake", cwd=str(home), max_usd=2)
    assert " started " in out, out
    job_id = out.split()[0]
    monkeypatch.setattr(sys, "argv", ["worker", job_id])
    # The real worker scrubs its environment; restore the test process afterward.
    with patch.dict(os.environ, dict(os.environ), clear=True):
        assert worker.main() == 0
    assert len(calls) == 1
    status = jobs.status(job_id)
    assert status["state"] == "done" and status["jev_review"]["state"] == "reviewed"
    if source == "env":
        assert not jev_config.key_path().exists()


@pytest.mark.parametrize(
    "state,retry,hints",
    [
        ("stopped", False, []),
        ("done", True, []),
        ("done", False, ["continue"]),
    ],
)
def test_stop_and_pending_continuations_do_not_schedule_paid_reviews(home, state, retry, hints):
    from subtask import auto_review, jev_config

    jev_config.configure(mode="auto")
    assert auto_review.prepare(1, state, retry, hints) is None


def test_wait_does_not_finish_between_hint_check_and_delivery(home, monkeypatch):
    from subtask import core, jobs

    folder = jobs.job_dir("st-hint-race")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"engine": "fake", "created": time.time()})
    jobs.update_status("st-hint-race", state="running", attempt=1, hints=["continue"])
    count = []

    def deliver(ids):
        count.append(1)
        # First pass sees a running attempt; it ends immediately after that check.
        jobs.update_status("st-hint-race", state="done", hints=["continue"] if len(count) == 1 else [])

    monkeypatch.setattr(core, "_deliver_hints", deliver)
    assert "st-hint-race done" in core.wait(["st-hint-race"], timeout=5)
    assert len(count) == 2
