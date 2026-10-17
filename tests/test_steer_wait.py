"""Wait modes, stall watchdog, steer and inspect, on the offline fake engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from .conftest import wait_final

# The fake engine gets the prompt as $1: "slow" sleeps, "quiet" sleeps without output.
SCRIPT = (
    'case "$1" in *slow*) sleep 30;; *quiet*) sleep 6;; *pause*) sleep 3;; esac; '
    'echo \'{"tool":"pytest","exit":0}\'; echo \'{"say":"## Summary\\n- did: \'"$1"\'"}\''
)


def _start(core, home, task, **kw):
    out = core.start(task, engine="fake", cwd=str(home), max_usd=0.5, **kw)
    assert " started " in out, out
    return out.split()[0]


def test_wait_any_returns_at_the_first_end(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    fast, slow = _start(core, home, "fast"), _start(core, home, "slow")
    t0 = time.time()
    out = core.wait([fast, slow], timeout=20, mode="any")
    assert time.time() - t0 < 15
    assert f"{fast} done" in out and f"{slow} running" in out
    core.stop(slow)


def test_first_success_stops_the_others(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    fast, slow = _start(core, home, "fast"), _start(core, home, "slow")
    out = core.wait([fast, slow], timeout=20, mode="first_success")
    assert out.startswith(f"first success: {fast} · stopped {slow}")
    assert jobs.status(slow)["state"] in jobs.FINAL


def test_first_success_skips_a_failed_verification(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    bad = _start(core, home, "fast", verify="false")
    good = _start(core, home, "pause", verify="true")
    out = core.wait([bad, good], timeout=20, mode="first_success")
    assert out.startswith(f"first success: {good}"), out


def test_first_success_with_no_winner_says_so(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "fast", verify="false")
    assert core.wait([job], timeout=20, mode="first_success").startswith("no job succeeded")


def test_bad_wait_mode_is_refused(home):
    from subtask import core

    assert core.wait(["st-aaaaaaaaaa"], mode="some").startswith("mode must be")


def test_a_silent_job_is_reported_stalled_and_keeps_running(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "quiet")
    time.sleep(1)
    out = core.wait([job], timeout=20, stall_timeout=1)
    assert out.startswith("stalled:"), out
    assert jobs.status(job)["state"] == "running"  # a label, never a kill
    assert wait_final(job)["state"] == "done"


def test_three_identical_errors_mark_a_job_stalled(home):
    from subtask import events, jobs, watchdog

    job = "st-0000000001"
    jobs.job_dir(job).mkdir(parents=True)
    status = {"state": "running", "updated": time.time()}
    for _ in range(3):
        events.emit(job, "tool", "Error: connection refused", source="adapter")
    assert watchdog.check(job, status).startswith("same error 3 times")
    events.emit(job, "tool", "editing core.py", source="adapter")
    assert watchdog.check(job, status) is None
    assert watchdog.check(job, {**status, "state": "done"}) is None


def test_steer_hint_is_delivered_when_the_attempt_ends(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "pause")
    assert "hint queued" in core.steer(job, "hint", "also update the README")
    out = core.wait([job], timeout=30)
    assert "running" in out or "done" in out
    final = wait_final(job)
    assert final["attempt"] == 2 and final["hints"] == []
    assert (jobs.job_dir(job) / "reply-2.md").read_text() == "also update the README"


def test_steer_abort_and_bad_action(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "slow")
    assert core.steer(job, "explode").startswith("action must be")
    assert core.steer(job, "hint").startswith("hint needs")
    assert core.steer(job, "abort") == f"{job} stopped"
    assert jobs.status(job)["state"] == "stopped"


def test_steer_retry_starts_a_fresh_job_with_the_instruction(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "slow")
    out = core.steer(job, "retry", "use the stdlib only")
    assert out.startswith(f"{job} retried as st-"), out
    new = out.split("retried as ")[1].split()[0]
    assert jobs.status(job)["state"] == "stopped"
    assert jobs.spec(new)["task"].endswith("Retry instructions: use the stdlib only")
    core.stop(new)


def test_inspect_tails_and_filters_the_raw_log(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    job = _start(core, home, "fast")
    wait_final(job)
    out = core.inspect(job)
    assert "output-1.jsonl:" in out and "pytest" in out
    assert "pytest" not in core.inspect(job, grep="summary").split("\n", 1)[1]
    assert core.inspect(job, tail=0).startswith("tail must be")
    assert core.inspect("st-ffffffffff").startswith("unknown job")


def test_jobs_outlive_the_server_and_wait_finds_them(home, monkeypatch):
    """The MCP server dies; the detached worker keeps going; a new process rejoins it."""
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SCRIPT)
    call = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "start", "arguments": {"task": "pause", "engine": "fake", "cwd": str(home),
                                                  "max_usd": 0.5}},
    }  # fmt: skip
    proc = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"],
        input=json.dumps(call) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ},
        check=False,
    )
    text = json.loads(proc.stdout.splitlines()[-1])["result"]["content"][0]["text"]
    job = text.split()[0]
    from subtask import jobs

    assert jobs.status(job)["state"] in jobs.ACTIVE  # the server is gone, the job is not
    later = subprocess.run(
        [sys.executable, "-c", f"from subtask import core; print(core.wait([{job!r}], timeout=30))"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ},
        check=False,
    )
    assert f"{job} done" in later.stdout, later.stdout + later.stderr
