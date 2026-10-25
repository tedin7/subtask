"""Adversarial regressions for automatic Jev reviews and credential sources."""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import pytest

from .test_delivery_review import answer

JOB = "st-jev-adversarial"


@pytest.fixture
def delivery(home, monkeypatch):
    from subtask import auto_review, jev_config, jobs

    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-jev-auto-secret-for-adversarial-tests")
    jev_config.configure(mode="auto")
    folder = jobs.job_dir(JOB)
    folder.mkdir(parents=True)
    jobs.write_json(
        folder / "spec.json",
        {"task": "Fix the parser", "engine": "fake", "max_usd": 2, "created": time.time()},
    )
    jobs.update_status(
        JOB,
        state="done",
        attempt=1,
        session_id="test-session",
        jev_review=auto_review.prepare(1, "done", False, []),
    )
    (folder / "result.md").write_text("Parser fixed; verification was not run.")
    return folder


def test_untrusted_provider_integer_cannot_escape_the_review_error_path(delivery, monkeypatch):
    """A malformed provider answer must stay unknown and leave no open reservation."""
    from subtask import delivery_review, ledger

    def provider(packet, key):
        body = answer(packet)
        first = next(iter(body["answers"].values()))
        first["probabilities"][next(iter(first["probabilities"]))] = 10**1000
        return body

    monkeypatch.setattr(delivery_review, "_request", provider)
    report = delivery_review.review(JOB, ["The parser works"], submit=True)
    assert report["status"] == "unknown"
    assert ledger.open_for(JOB) == []


def test_default_env_file_credential_is_not_read_through_a_symlink(home, monkeypatch):
    """The unset SUBTASK_ENV_FILE path must not follow a symlink to a key file."""
    from subtask import config, jev_config

    monkeypatch.delenv("SUBTASK_ENV_FILE")
    target = home / "legacy-env-target"
    target.write_text("TYPESAFE_API_KEY=fake-legacy-env-secret\n")
    target.chmod(0o600)
    link = home / "legacy-env-link"
    link.symlink_to(target)
    monkeypatch.setattr(config, "ENV_FILE", link)

    assert jev_config.key_status()["configured"] is False
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()


def test_env_file_key_must_not_be_world_readable(home, monkeypatch):
    """A legacy env file that carries the Jev key must be private, like other key files."""
    from subtask import jev_config

    path = home / "world-readable-env"
    path.write_text("TYPESAFE_API_KEY=fake-world-readable-jev-secret\n")
    path.chmod(0o644)
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(path))

    assert jev_config.key_status()["configured"] is False
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()


def test_exhausted_verification_retry_still_triggers_auto_review(home, monkeypatch):
    """A final verification failure with no retry left is a delivery to review."""
    from subtask import auto_review, config, core, delivery_review, jev_config, jobs, procid, worker

    key = "fake-managed-jev-key-for-exhausted-retry"
    jev_config.configure(mode="auto", api_key=key)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"parser fixed"}\'')
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
    out = core.start("Fix the parser", engine="fake", cwd=str(home), max_usd=2, verify="false", max_retries=0)
    assert " started " in out, out
    job_id = out.split()[0]
    monkeypatch.setattr(sys, "argv", ["worker", job_id])
    with patch.dict(os.environ, dict(os.environ), clear=True):
        assert worker.main() == 0

    status = jobs.status(job_id)
    assert status["state"] == "done"
    assert status["verification"]["exit_code"] == 1
    assert status["retry_pending"] is False
    assert len(calls) == 1
    assert status["jev_review"]["state"] == "reviewed"


def test_malformed_config_home_does_not_abort_worker_finalization(home, monkeypatch):
    """An unusable Jev config path must not lose a successful worker delivery."""
    from subtask import config, core, jobs, procid, worker

    monkeypatch.setenv("SUBTASK_JEV_CONFIG", "~no-such-user-for-jev-tests/jev.json")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"parser fixed"}\'')
    monkeypatch.setattr(config, "DSH_KEY_FILE", home / "missing-deepseek")
    monkeypatch.setattr(core, "_spawn", lambda *_: procid.identity())
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    out = core.start("Fix the parser", engine="fake", cwd=str(home), max_usd=2)
    assert " started " in out, out
    job_id = out.split()[0]
    monkeypatch.setattr(sys, "argv", ["worker", job_id])
    with patch.dict(os.environ, dict(os.environ), clear=True):
        try:
            code = worker.main()
        except RuntimeError:
            code = None

    folder = jobs.job_dir(job_id)
    status = jobs.read_json(folder / "status.json")
    assert code == 0
    assert status["state"] == "done"
    assert (folder / "result.md").read_text() == "parser fixed"


def test_cli_never_echoes_a_mistyped_secret_argument(monkeypatch, capsys):
    """The setup parser must reject a key argument without printing its value."""
    from subtask import cli

    secret = "fake-mistyped-secret-value"
    monkeypatch.setattr(sys, "argv", ["subtask", "jev", "setup", "--key", secret])
    with pytest.raises(SystemExit):
        cli.main()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_progress_stops_saying_in_progress_after_an_interrupted_review(delivery):
    """An interrupted review must not leave a permanent 'in progress' progress line."""
    from subtask import jobs, progress

    reporter = progress.Reporter(lambda *_: None, ids=[JOB])
    reporter.poll()
    assert reporter.states[JOB] == "jev"
    status = jobs.status(JOB)
    jobs.update_status(JOB, jev_review={**status["jev_review"], "state": "unknown", "reason": "interrupted"})

    reporter.poll()
    assert "in corso" not in reporter.summary()


def test_huge_pending_timestamp_is_rejected_instead_of_crashing_wait(delivery):
    """Durable metadata is untrusted: an out-of-range timestamp must not raise."""
    from subtask import auto_review, jobs

    status = jobs.status(JOB)
    jobs.update_status(JOB, jev_review={**status["jev_review"], "created": 10**1000})

    assert auto_review.pending(JOB, jobs.status(JOB)) is False
    assert jobs.status(JOB)["jev_review"]["state"] == "unknown"
