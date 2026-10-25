"""Adversarial checks for the advisory Jev delivery review.

These tests are deliberately red against the reviewed commit: each one names a
way the review can execute candidate-controlled code, bind advice to the wrong
candidate, spend on a non-terminal job, or leak a credential.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time

import pytest

from subtask import jobs


@pytest.fixture
def delivery(home, monkeypatch):
    from subtask import delivery_review

    monkeypatch.setenv("SUBTASK_JEV_KEY_FILE", str(home / "missing-jev-key"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(delivery_review, "_key", lambda: "test-key-not-real")
    folder = jobs.job_dir("st-delivery")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "Fix the parser and update the UI", "engine": "fake"})
    jobs.write_json(folder / "status.json", {"state": "done", "attempt": 1, "verification": None})
    (folder / "result.md").write_text("Everything passes; parser fixed. UI not exercised.")
    return folder


def answer(packet, choice="unproven", evidence="worker_report"):
    answers = {}
    for name, question in packet["questions"].items():
        selected = evidence if name.endswith("_evidence") else choice
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": {key: float(key == selected) for key in question["criteria"]},
        }
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 50, "output_tokens": 12}}


def _git_repo(home):
    repo = home / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    (repo / "parser.py").write_text("value = 1\n")
    git("add", "parser.py")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    (repo / "parser.py").write_text("value = 2\n")
    git("commit", "-qam", "candidate")
    head = git("rev-parse", "HEAD")
    return repo, git, base, head


def test_prepare_does_not_run_candidate_git_commands(delivery, home):
    """Preparing evidence is advertised as observation-only: no candidate code runs."""
    from subtask import delivery_review as review

    repo, git, base, _ = _git_repo(home)
    branch = git("branch", "--show-current")
    sentinel = home / "fsmonitor-ran"
    hook = home / "fsmonitor.sh"
    hook.write_text("#!/bin/sh\ntouch " + shlex.quote(str(sentinel)) + "\nexit 0\n")
    hook.chmod(0o755)
    git("config", "core.fsmonitor", str(hook))
    sentinel.unlink(missing_ok=True)  # any setup command is not the review
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": "Fix parser",
            "engine": "fake",
            "repo": str(repo),
            "cwd": str(repo),
            "branch": branch,
            "base": base,
        },
    )
    report = review.review("st-delivery", ["Parser works"])
    assert report["status"] == "prepared"
    assert not sentinel.exists(), "offline prepare executed the candidate's core.fsmonitor command"


def test_review_refuses_a_delivery_with_a_pending_retry(delivery, monkeypatch):
    """A done job waiting for a retry slot is not the terminal delivery the contract promises."""
    from subtask import delivery_review as review
    from subtask import verifier

    status = jobs.read_json(delivery / "status.json")
    status.update(state="done", attempt=1, retry_pending=True, updated=time.time())
    jobs.write_json(delivery / "status.json", status)
    assert verifier.pending(status) is True
    calls = []

    def provider(packet, _):
        calls.append(packet)
        return answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    with pytest.raises(ValueError, match="terminal"):
        review.review("st-delivery", ["UI works"], submit=True)
    assert calls == []


def test_candidate_change_hidden_by_redaction_is_not_reviewed(delivery, home, monkeypatch):
    """A candidate that changes during the call must not receive a verdict, even if only a secret changed."""
    from subtask import delivery_review as review

    repo, git, base, head = _git_repo(home)
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": "Fix parser",
            "engine": "fake",
            "repo": str(repo),
            "cwd": str(repo),
            "branch": git("branch", "--show-current"),
            "base": base,
            "verify": "pytest -q",
        },
    )
    jobs.write_json(
        delivery / "status.json",
        {
            "state": "done",
            "attempt": 1,
            "verification": {
                "exit_code": 0,
                "head": head,
                "dirty_after": False,
                "command": "pytest -q",
            },
        },
    )
    first_token = "sk-ant-" + "A" * 32
    second_token = "sk-ant-" + "B" * 32
    (delivery / "result.md").write_text("Parser fixed; token=" + first_token + "\n")

    def provider(packet, _):
        (delivery / "result.md").write_text("Parser fixed; token=" + second_token + "\n")
        return answer(packet, "supported", "verification")

    monkeypatch.setattr(review, "_request", provider)
    report = review.review("st-delivery", ["Parser fixed"], submit=True)
    assert report["status"] == "unknown", (
        "the review attached a verdict to a candidate changed during the call"
    )


def test_provider_cannot_echo_the_key_into_the_receipt(delivery, monkeypatch):
    """Success bodies are persisted and returned, so they need the same redaction as errors."""
    from subtask import delivery_review as review

    secret = "test-key-not-real"

    def provider(packet, _):
        body = answer(packet)
        body["model"] = "jev-" + secret
        body["usage"]["echo"] = secret
        return body

    monkeypatch.setattr(review, "_request", provider)
    report = review.review("st-delivery", ["UI works"], submit=True)
    assert report["status"] == "reviewed"
    receipt = delivery / "reviews" / f"{report['request_sha256']}.json"
    serialized = json.dumps(report) + receipt.read_text()
    assert secret not in serialized, "provider-controlled fields bypassed credential redaction"


def test_known_secret_with_a_json_escaped_character_is_redacted(delivery, home):
    """Redacting the serialized JSON misses values that JSON escapes, such as a backslash."""
    from subtask import delivery_review as review

    secret = "abc\\defgh"
    env = home / "env"
    env.write_text(env.read_text() + "CUSTOM_SECRET=" + secret + "\n")
    (delivery / "result.md").write_text("Parser fixed; value " + secret + "\n")
    report = review.review("st-delivery", ["Parser fixed"])
    packet = json.loads(
        (delivery / "reviews" / f"{report['request_sha256']}.request.json").read_text()
    )
    text = packet["state"]["materials"]["worker_report"]["text"]
    assert secret not in text, "a known secret was transmitted because JSON escaped one of its characters"


@pytest.mark.parametrize("case", ["missing_dirty", "wrong_command", "oracle_mismatch"])
def test_verification_must_be_complete_and_match_the_job(delivery, home, monkeypatch, case):
    """A positive verification is only clean when its dirtiness, command and oracle agree."""
    from subtask import delivery_review as review

    repo, git, base, head = _git_repo(home)
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": "Fix parser",
            "engine": "fake",
            "repo": str(repo),
            "cwd": str(repo),
            "branch": git("branch", "--show-current"),
            "base": base,
            "verify": "pytest -q",
            "oracle_cmd": "python bench.py",
        },
    )
    verification = {
        "exit_code": 0,
        "head": head,
        "dirty_after": False,
        "command": "pytest -q",
    }
    if case == "missing_dirty":
        verification.pop("dirty_after")
    elif case == "wrong_command":
        verification["command"] = "true"
    else:
        verification["oracle"] = {"exit_match": True, "output_match": False}
    jobs.write_json(
        delivery / "status.json",
        {"state": "done", "attempt": 1, "verification": verification},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: answer(packet, "supported", "verification"))
    report = review.review("st-delivery", ["Parser works"], submit=True)
    limitations = set(report["limitations"])
    assert (
        report["assessments"][0]["assessment"] == "unproven"
        or limitations & {"verification_dirty_unknown", "verification_command_mismatch", "oracle_mismatch"}
    )


def test_tied_probabilities_are_not_a_supported_verdict(delivery, home, monkeypatch):
    """A tied provider answer is ambiguous; it must not become a definite support."""
    from subtask import delivery_review as review

    repo, git, base, head = _git_repo(home)
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": "Fix parser",
            "engine": "fake",
            "repo": str(repo),
            "cwd": str(repo),
            "branch": git("branch", "--show-current"),
            "base": base,
            "verify": "pytest -q",
        },
    )
    jobs.write_json(
        delivery / "status.json",
        {
            "state": "done",
            "attempt": 1,
            "verification": {
                "exit_code": 0,
                "head": head,
                "dirty_after": False,
                "command": "pytest -q",
            },
        },
    )

    def provider(packet, _):
        body = answer(packet)
        first = body["answers"]["C1"]
        first["choice"] = "supported"
        first["probabilities"] = {"supported": 1 / 3, "contradicted": 1 / 3, "unproven": 1 / 3}
        evidence = body["answers"]["C1_evidence"]
        evidence["choice"] = "verification"
        evidence["probabilities"] = {
            key: 1 / len(evidence["probabilities"]) for key in evidence["probabilities"]
        }
        return body

    monkeypatch.setattr(review, "_request", provider)
    report = review.review("st-delivery", ["Parser works"], submit=True)
    assert report["status"] == "unknown" or report["assessments"][0]["assessment"] != "supported"


def test_review_material_is_the_original_request_not_the_worker_prompt(delivery):
    """The scope check claims to use the original request; spec.task also contains harness text."""
    from subtask import core
    from subtask import delivery_review as review

    original = "Fix the parser"
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": original + core.WORKTREE_NOTE + "st-x. Commit your work there; never push.",
            "engine": "fake",
        },
    )
    report = review.review("st-delivery", ["Parser fixed"])
    packet = json.loads(
        (delivery / "reviews" / f"{report['request_sha256']}.request.json").read_text()
    )
    assert packet["state"]["materials"]["request"]["text"] == original


def test_rewritten_job_spec_is_accepted_as_the_original_request(delivery):
    """spec.json is worker-writable metadata; a rewrite before review erases requirements from scope."""
    from subtask import delivery_review as review

    original = "Fix the parser and update the UI"
    jobs.write_json(delivery / "spec.json", {"task": original, "engine": "fake"})
    # The worker runs as the same user and learns the job id from its worktree/branch.
    jobs.write_json(delivery / "spec.json", {"task": "Fix the parser", "engine": "fake"})
    report = review.review("st-delivery", ["Parser fixed"])
    packet = json.loads(
        (delivery / "reviews" / f"{report['request_sha256']}.request.json").read_text()
    )
    assert "update the UI" in packet["state"]["materials"]["request"]["text"]
