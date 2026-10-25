"""Targeted adversarial checks for the v5 fixes in 8434b44.

Only temporary repositories and fake provider bodies are used: no network, no
real credentials.  These tests exercise the exact claims of the v5 correction
and the remaining bypasses an independent reviewer can reproduce.
"""

from __future__ import annotations

import json
import subprocess
import zlib
from pathlib import Path

import pytest

from subtask import jobs

DELIVERY = "st-delivery"


@pytest.fixture(autouse=True)
def _fresh_redaction_set():
    from subtask import redact

    redact._known.clear()
    yield
    redact._known.clear()


def _run_git(
    cwd: Path, *args: str, check: bool = True, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        check=check,
        timeout=30,
        env=env,
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
    return {
        "exit_code": 0,
        "head": head,
        "dirty_after": False,
        "command": "pytest -q",
        "output_tail": "parser: 1 passed",
        **extra,
    }


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


def _submodule_repo(home: Path) -> tuple[Path, str, str]:
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
    (repo / "sm" / "f").write_text("two\n")
    _commit(repo / "sm", "two")
    _run_git(repo, "add", "sm")
    _commit(repo, "candidate")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _packet(folder: Path, report: dict) -> dict:
    return json.loads((folder / "reviews" / f"{report['request_sha256']}.request.json").read_text())


def test_gitlink_changes_block_supported_and_stay_visible(home, monkeypatch):
    """A gitlink is visible and its unexpanded content cannot be promoted."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _submodule_repo(home)
    _run_git(repo, "config", "submodule.sm.ignore", "all")
    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "patch"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    patch = _packet(folder, report)["state"]["materials"]["patch"]["text"]
    assert "Subproject commit" in patch, "a gitlink change was hidden from the patch"
    assert "patch_content_not_inspected" in report["limitations"]
    assert report["assessments"][0]["assessment"] != "supported"


def test_binary_patch_blocks_supported(home, monkeypatch):
    """Binary content is not expanded; the limitation must block a positive verdict."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo = home / "repo"
    _init(repo)
    (repo / "blob.bin").write_bytes(b"\x00base\x00")
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "blob.bin").write_bytes(b"\x00candidate\x00")
    _commit(repo, "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "patch"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    patch = _packet(folder, report)["state"]["materials"]["patch"]["text"]
    assert "GIT binary patch" in patch
    assert "patch_content_not_inspected" in report["limitations"]
    assert report["assessments"][0]["assessment"] != "supported"


def test_report_flag_blocks_scope_too(home, monkeypatch):
    """The unexpanded-content limitation also blocks the whole-request scope."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo = home / "repo"
    _init(repo)
    (repo / "blob.bin").write_bytes(b"\x00base\x00")
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "blob.bin").write_bytes(b"\x00candidate\x00")
    _commit(repo, "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "patch"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert report["scope_assessment"]["assessment"] != "supported"


def test_core_use_replace_refs_cannot_reenable_replacement(home, monkeypatch):
    """`core.useReplaceRefs=true` must not override the hardened Git environment."""
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
    _run_git(repo, "config", "core.useReplaceRefs", "true")

    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    report = review.review(DELIVERY, ["Parser fixed"])
    patch = _packet(folder, report)["state"]["materials"]["patch"]["text"]
    assert "BAD" in patch
    assert "GOOD" not in patch


def test_submodule_recurse_and_named_ignore_do_not_run_nested_diff(home, monkeypatch):
    """Neither submodule.recurse nor submodule.<name>.ignore re-enables nested diffs."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _submodule_repo(home)
    _run_git(repo, "config", "diff.submodule", "diff")
    _run_git(repo, "config", "submodule.recurse", "true")
    _run_git(repo, "config", "submodule.sm.ignore", "all")
    sentinel = home / "submodule-diff-ran"
    script = home / "ext.sh"
    script.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\necho external\n")
    script.chmod(0o755)
    _run_git(repo / ".git" / "modules" / "sm", "config", "diff.external", str(script))
    sentinel.unlink(missing_ok=True)

    _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    report = review.review(DELIVERY, ["Parser fixed"])
    assert report["status"] == "prepared"
    assert not sentinel.exists()
    assert (
        "Subproject commit"
        in _packet(
            _job(
                home,
                _git_spec(repo, base),
                {"state": "done", "attempt": 1, "verification": _verification(head)},
            ),
            report,
        )["state"]["materials"]["patch"]["text"]
    )


def test_per_driver_external_diff_is_not_executed(home, monkeypatch):
    """A diff driver command is an external diff and must stay disabled."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    (repo / ".gitattributes").write_text("parser.py diff=candidate\n")
    _commit(repo, "attributes")
    sentinel = home / "driver-ran"
    script = home / "driver.sh"
    script.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\necho external\n")
    script.chmod(0o755)
    _run_git(repo, "config", "diff.candidate.command", str(script))
    sentinel.unlink(missing_ok=True)

    _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    report = review.review(DELIVERY, ["Parser fixed"])
    assert report["status"] == "prepared"
    assert not sentinel.exists()


def test_reservation_uses_only_process_allowance(home, monkeypatch):
    """The reservation is min(DEFAULT_MAX_USD, MAX_JOB_USD), not the job's max_usd."""
    from subtask import config, ledger
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    monkeypatch.setattr(config, "DEFAULT_MAX_USD", 1.5)
    monkeypatch.setattr(config, "MAX_JOB_USD", 0.5)
    _job(
        home,
        _git_spec(repo, base, max_usd=9),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert report["budget_charged_usd"] == 0.5
    assert ledger.committed() == 0.5


def test_low_job_budget_blocks_before_any_reservation(home, monkeypatch):
    """A lower max_usd blocks submission and leaves the process allowance intact."""
    from subtask import config, ledger
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    folder = _job(
        home,
        _git_spec(repo, base, max_usd=0.01),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    calls = []
    monkeypatch.setattr(review, "_request", lambda packet, _: calls.append(1) or _answer(packet))
    with pytest.raises(ValueError, match="budget"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert calls == []
    assert ledger.committed() == 0
    prepared = review.review(DELIVERY, ["Parser fixed"], original_request="Fix parser")
    assert _packet(folder, prepared)["state"]["review_allowance_usd"] == min(
        config.DEFAULT_MAX_USD, config.MAX_JOB_USD
    )


def test_recovery_without_key_redacts_the_receipt_on_disk(home, monkeypatch):
    """Recovery without credentials must also clean the stored receipt, not only the return value."""
    from subtask import delivery_review as review

    secret = "cache-" + "x" * 16
    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
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
    receipt = jobs.job_dir(DELIVERY) / "reviews" / f"{first['binding_sha256']}.json"
    assert secret in receipt.read_text()

    with (home / "env").open("a") as stream:
        stream.write("CACHED_SECRET=" + secret + "\n")

    def unavailable():
        raise ValueError("credential removed")

    monkeypatch.setattr(review, "_key", unavailable)
    second = review.review(DELIVERY, ["Parser fixed"], submit=True)
    assert secret not in json.dumps(second)
    assert secret not in receipt.read_text()


def test_invalid_utf8_diff_is_rejected(home, monkeypatch):
    """A non-UTF-8 diff is refused instead of being decoded with replacement."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo = home / "repo"
    _init(repo)
    (repo / "f").write_bytes(b"base\n")
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "f").write_bytes(b"caf\xe9\n")
    _commit(repo, "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    with pytest.raises(ValueError):
        review.review(DELIVERY, ["Parser fixed"])


@pytest.mark.parametrize("marker", ["FAILED", "FAILURES", "AssertionError"])
def test_ansi_wrapped_failure_marker_is_not_promoted(home, monkeypatch, marker):
    """`\\b` does not match a marker preceded by an ANSI colour escape ending in 'm'."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    output = "\x1b[31m" + marker + "\x1b[0m test_ui"
    _job(
        home,
        _git_spec(repo, base),
        {
            "state": "done",
            "attempt": 1,
            "verification": _verification(head, output_tail=output),
        },
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "verification"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert "verification_output_conflict" in report["limitations"]
    assert report["assessments"][0]["assessment"] != "supported"


def test_lowercase_failure_summary_is_not_promoted(home, monkeypatch):
    """Pytest's normal summary is lowercase 'failed'; it is still a failure marker."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    _job(
        home,
        _git_spec(repo, base),
        {
            "state": "done",
            "attempt": 1,
            "verification": _verification(head, output_tail="1 failed, 2 passed in 0.10s"),
        },
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: _answer(packet, "supported", "verification"))
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert report["assessments"][0]["assessment"] != "supported"


def test_substituted_loose_object_cannot_rewrite_the_committed_patch(home, monkeypatch):
    """GIT_NO_REPLACE_OBJECTS does not stop a hash-mismatched loose object from being read."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base, head = _repo(home)
    blob = _git(repo, "rev-parse", f"{head}:parser.py")
    object_path = repo / ".git" / "objects" / blob[:2] / blob[2:]
    assert object_path.is_file(), "the fresh commit must use a loose blob"
    replacement = b"value = 3\n"
    object_path.write_bytes(zlib.compress(b"blob %d\x00" % len(replacement) + replacement))
    corrupt = _run_git(repo, "fsck", "--no-dangling", check=False)
    assert "hash-path mismatch" in corrupt.stderr or "hash-path mismatch" in corrupt.stdout

    folder = _job(
        home,
        _git_spec(repo, base),
        {"state": "done", "attempt": 1, "verification": _verification(head)},
    )
    report = review.review(DELIVERY, ["Parser fixed"])
    patch = _packet(folder, report)["state"]["materials"]["patch"]["text"]
    assert report["candidate"]["head"] == head
    assert "value = 2" in patch, "the review read a substituted object instead of the committed blob"
    assert "value = 3" not in patch


def test_symlinked_home_parent_is_rejected(home, monkeypatch):
    """A symlink above the jobs root also redirects reads and receipt writes."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    root = home / "home"
    real = home / "realhome"
    real.mkdir()
    if root.exists():
        root.rmdir()
    root.symlink_to(real, target_is_directory=True)
    _job(
        home, {"task": "Fix parser", "engine": "fake"}, {"state": "done", "attempt": 1, "verification": None}
    )
    with pytest.raises(ValueError):
        review.review(DELIVERY, ["Parser fixed"])


def test_stale_request_with_now_known_secret_is_cleaned(home, monkeypatch):
    """A request persisted before a secret became known must not keep it forever."""
    from subtask import delivery_review as review

    secret = "late-" + "y" * 16
    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    folder = _job(
        home,
        {"task": "Fix parser", "engine": "fake"},
        {"state": "done", "attempt": 1, "verification": None},
        result="Parser fixed; token " + secret + "\n",
    )
    first = review.review(DELIVERY, ["Parser fixed"])
    first_request = folder / "reviews" / f"{first['request_sha256']}.request.json"
    assert secret in first_request.read_text()

    with (home / "env").open("a") as stream:
        stream.write("LATE_SECRET=" + secret + "\n")
    review.review(DELIVERY, ["Parser fixed"])
    assert secret not in first_request.read_text()
