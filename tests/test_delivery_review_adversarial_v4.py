"""Adversarial checks for the v4 Jev review fixes (candidate deb485f).

These tests are deliberately red against the reviewed candidate: they exercise
the paths the v4 commit claims to close but still leaves open.  They use only
temporary repositories and fake provider bodies; no network and no real key.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from subtask import jobs

DELIVERY = "st-delivery"


@pytest.fixture(autouse=True)
def _fresh_redaction_set():
    """The redaction set is process-global; keep one test from masking another."""
    from subtask import redact

    redact._known.clear()
    yield
    redact._known.clear()


def _run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        check=check,
        timeout=30,
    )


def _git(cwd: Path, *args: str) -> str:
    return _run_git(cwd, *args).stdout.strip()


def _init(path: Path) -> None:
    path.mkdir()
    _run_git(path, "init", "-q")
    _run_git(path, "config", "user.email", "test@example.test")
    _run_git(path, "config", "user.name", "Test")


def _commit(path: Path, message: str) -> None:
    _run_git(path, "add", "-A")
    _run_git(path, "commit", "-qm", message)


def _repo(home: Path) -> tuple[Path, str, str]:
    """A tiny repository with a base commit and a candidate commit."""
    repo = home / "repo"
    _init(repo)
    (repo / "parser.py").write_text("value = 1\n")
    _commit(repo, "baseline")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "parser.py").write_text("value = 2\n")
    _commit(repo, "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _job(home: Path, spec: dict, status: dict, result: str = "done") -> Path:
    folder = jobs.job_dir(DELIVERY)
    folder.mkdir(parents=True, exist_ok=True)
    jobs.write_json(folder / "spec.json", spec)
    jobs.write_json(folder / "status.json", status)
    (folder / "result.md").write_text(result)
    return folder


def _answer(packet: dict, choice: str = "unproven", evidence: str = "worker_report") -> dict:
    answers = {}
    for name, question in packet["questions"].items():
        selected = evidence if name.endswith("_evidence") else choice
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": {key: float(key == selected) for key in question["criteria"]},
        }
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 50, "output_tokens": 12}}


def _verification(head: str, **extra) -> dict:
    return {"exit_code": 0, "head": head, "dirty_after": False, "command": "pytest -q", **extra}


def _git_spec(repo: Path, base: str, **extra) -> dict:
    return {
        "task": "Fix parser",
        "engine": "fake",
        "repo": str(repo),
        "cwd": str(repo),
        "branch": _git(repo, "branch", "--show-current"),
        "base": base,
        "verify": "pytest -q",
        **extra,
    }


def _submodule_repo(home: Path) -> tuple[Path, str]:
    """A repository whose candidate commit advances a submodule."""
    source = home / "subsource"
    _init(source)
    (source / "f").write_text("one\n")
    _commit(source, "one")

    repo = home / "repo"
    _init(repo)
    subprocess.check_call(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(repo),
            "submodule",
            "add",
            "-q",
            str(source),
            "sm",
        ]
    )
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")
    _run_git(repo / "sm", "config", "user.name", "Test")
    _run_git(repo / "sm", "config", "user.email", "test@example.test")
    (repo / "sm" / "f").write_text("two\n")
    _commit(repo / "sm", "two")
    _run_git(repo, "add", "sm")
    _commit(repo, "candidate")
    return repo, base


def test_prepare_does_not_execute_a_submodule_external_diff(home, monkeypatch):
    """`--no-ext-diff` is not propagated to the nested `git diff` of a submodule."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base = _submodule_repo(home)

    _run_git(repo, "config", "diff.submodule", "diff")
    sentinel = home / "submodule-diff-ran"
    script = home / "ext.sh"
    script.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\necho external\n")
    script.chmod(0o755)
    _run_git(repo / ".git" / "modules" / "sm", "config", "diff.external", str(script))
    sentinel.unlink(missing_ok=True)

    _job(home, _git_spec(repo, base), {"state": "done", "attempt": 1, "verification": None})
    report = review.review(DELIVERY, ["Parser works"])
    assert report["status"] == "prepared"
    assert not sentinel.exists(), "offline prepare executed the candidate submodule's diff.external"


def test_submodule_changes_cannot_be_hidden_from_the_patch(home, monkeypatch):
    """`diff.ignoreSubmodules=all` hides even the gitlink from the candidate patch."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base = _submodule_repo(home)
    _run_git(repo, "config", "diff.ignoreSubmodules", "all")
    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": None},
    )
    report = review.review(DELIVERY, ["Parser works"])
    assert report["status"] == "prepared"
    packet = json.loads((folder / "reviews" / f"{report['request_sha256']}.request.json").read_text())
    patch = packet["state"]["materials"]["patch"]["text"]
    assert "sm" in patch, "a candidate-configured ignoreSubmodules hid a submodule change"


def test_git_replace_refs_cannot_rewrite_the_committed_patch(home, monkeypatch):
    """refs/replace makes `git diff H` show another commit while candidate.head stays H."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo = home / "repo"
    _init(repo)
    (repo / "f").write_text("base\n")
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "f").write_text("BAD\n")
    _commit(repo, "bad")
    head = _git(repo, "rev-parse", "HEAD")

    _run_git(repo, "checkout", "-q", "-b", "replacement", base)
    (repo / "f").write_text("GOOD\n")
    _commit(repo, "good")
    replacement = _git(repo, "rev-parse", "HEAD")
    _run_git(repo, "checkout", "-q", "-")
    _run_git(repo, "replace", head, replacement)

    real = _run_git(repo, "--no-replace-objects", "show", f"{head}:f").stdout.strip()
    assert real == "BAD"

    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )

    def provider(packet, _):
        patch = packet["state"]["materials"]["patch"]["text"]
        return _answer(packet, "supported" if "GOOD" in patch else "unproven", "patch")

    monkeypatch.setattr(review, "_request", provider)
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    packet = json.loads((folder / "reviews" / f"{report['request_sha256']}.request.json").read_text())
    patch = packet["state"]["materials"]["patch"]["text"]
    assert "BAD" in patch, "the review read a replace-ref patch instead of the committed tree"
    assert "GOOD" not in patch
    assert report["assessments"][0]["assessment"] != "supported"


@pytest.mark.parametrize("output_tail", [None, "test_ui FAILED"])
def test_verification_output_must_be_present_and_consistent(home, monkeypatch, output_tail):
    """The modified positive test leaves output_tail=FAILED while exit_code becomes 0."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    verification = _verification(head)
    if output_tail is not None:
        verification["output_tail"] = output_tail
    _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": verification},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "verification"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert report["assessments"][0]["assessment"] != "supported", (
        "a verification without output, or whose output says FAILED, was promoted"
    )


def test_oracle_result_must_come_from_the_recorded_oracle_command(home, monkeypatch):
    """The oracle is checked for flags but not for the command that produced it."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    verification = _verification(
        head,
        oracle={"command": "true", "exit_match": True, "output_match": True},
    )
    _job(
        home,
        _git_spec(repo, base, oracle_cmd="python bench.py"),
        {"state": "done", "attempt": 1, "verification": verification},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "verification"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert "oracle_mismatch" in report["limitations"]
    assert report["assessments"][0]["assessment"] != "supported"


def test_cached_receipt_is_redacted_when_a_secret_becomes_known(home, monkeypatch):
    """A cached receipt is returned before `_clean`, so a now-known key survives it."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    secret = "cache-" + "x" * 16
    _job(
        home,
        {"task": "Fix parser", "engine": "fake"},
        {"state": "done", "attempt": 1, "verification": None},
    )

    def provider(packet, _):
        body = _answer(packet)
        body["model"] = "jev-" + secret
        return body

    monkeypatch.setattr(review, "_request", provider)
    first = review.review(DELIVERY, ["Parser fixed"], submit=True)
    assert first["status"] == "reviewed"
    assert secret in first["model"]

    with (home / "env").open("a") as stream:
        stream.write("CACHED_SECRET=" + secret + "\n")
    second = review.review(DELIVERY, ["Parser fixed"], submit=True)
    assert secret not in json.dumps(second), (
        "the cached receipt was returned without redacting a secret now loaded by Subtask"
    )


def test_intent_receipt_does_not_persist_a_known_key(home, monkeypatch):
    """The pre-provider intent is saved before `_clean`; a crash leaves the key on disk."""
    from subtask import delivery_review as review

    key = "test-key-not-real"
    monkeypatch.setattr(review, "_key", lambda: key)
    folder = _job(
        home,
        {"task": "Fix parser", "engine": "fake"},
        {"state": "done", "attempt": key, "verification": None},
    )

    def provider(*_):
        raise SystemExit(1)

    monkeypatch.setattr(review, "_request", provider)
    with pytest.raises(SystemExit):
        review.review(DELIVERY, ["Parser fixed"], submit=True)

    receipts = [
        path for path in (folder / "reviews").glob("*.json") if not path.name.endswith(".request.json")
    ]
    assert receipts
    persisted = "\n".join(path.read_text() for path in receipts)
    assert key not in persisted, "the durable intent persisted the credential before redaction"


def test_symlinked_jobs_root_is_rejected(home, monkeypatch):
    """The checks cover the leaf job/review directories, not a symlinked jobs root."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    root = home / "home"
    root.mkdir()
    fake = home / "fakejobs"
    fake.mkdir()
    (root / "jobs").symlink_to(fake, target_is_directory=True)
    _job(
        home,
        {"task": "FAKE TASK", "engine": "fake"},
        {"state": "done", "attempt": 1, "verification": None},
    )
    with pytest.raises(ValueError):
        review.review(DELIVERY, ["Parser fixed"])


def test_job_budget_below_review_allowance_is_not_silently_underreserved(home, monkeypatch):
    """`max_usd` is worker-writable; using it as the reserve lets the monthly cap be bypassed."""
    from subtask import config
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    _job(
        home,
        {"task": "Fix parser", "engine": "fake", "max_usd": 0.01},
        {"state": "done", "attempt": 1, "verification": None},
    )
    calls = []

    def provider(packet, _):
        calls.append(1)
        return _answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    refused = False
    try:
        report = review.review(DELIVERY, ["Parser fixed"], submit=True)
    except ValueError as exc:
        assert "budget" in str(exc)
        refused = True
    if not refused:
        assert report["budget_charged_usd"] >= config.DEFAULT_MAX_USD, (
            "a 0.01 $ job budget under-reserved an unknown-cost provider call"
        )
    if calls:
        assert refused or report["budget_charged_usd"] >= config.DEFAULT_MAX_USD


def test_non_string_task_is_rejected_not_crashing(home, monkeypatch):
    """The v4 `.split(WORKTREE_NOTE)` assumes spec.task is a string."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    _job(
        home,
        {"task": 123, "engine": "fake"},
        {"state": "done", "attempt": 1, "verification": None},
    )
    with pytest.raises(ValueError):
        review.review(DELIVERY, ["Parser fixed"])
