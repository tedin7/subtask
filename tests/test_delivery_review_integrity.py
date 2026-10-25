"""Integrity, bounded-work and late-redaction checks for the v6 delivery review.

Only temporary repositories and fake provider bodies: no network, no real credentials.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import zlib
from pathlib import Path

import pytest

from subtask import jobs

DELIVERY = "st-integrity"


@pytest.fixture(autouse=True)
def _fresh_redaction_set():
    from subtask import redact

    redact._known.clear()
    yield
    redact._known.clear()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], text=True, capture_output=True, check=True, timeout=30
    ).stdout.strip()


def _raw(cwd: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, timeout=30).stdout


def _repo(home: Path, files: dict[str, str], changes: dict[str, str], *init: str) -> tuple[Path, str, str]:
    repo = home / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", *init)
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    commits = []
    for snapshot in (files, changes):
        for name, text in snapshot.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "snapshot")
        commits.append(_git(repo, "rev-parse", "HEAD"))
    return repo, commits[0], commits[1]


def _overwrite(repo: Path, oid: str, kind: bytes, content: bytes) -> None:
    """Store `content` under the path of `oid`, leaving the id unchanged."""
    path = repo / ".git" / "objects" / oid[:2] / oid[2:]
    assert path.is_file(), "the fresh commit must use a loose object"
    path.chmod(0o644)
    path.write_bytes(zlib.compress(kind + b" %d\x00" % len(content) + content))


def _job(repo: Path, base: str, head: str, **status_extra) -> Path:
    folder = jobs.job_dir(DELIVERY)
    folder.mkdir(parents=True, exist_ok=True)
    spec = {
        "task": "Fix parser",
        "engine": "fake",
        "repo": str(repo),
        "cwd": str(repo),
        "branch": _git(repo, "branch", "--show-current"),
        "base": base,
        "verify": "pytest -q",
    }
    verification = {
        "exit_code": 0,
        "head": head,
        "dirty_after": False,
        "command": "pytest -q",
        "output_tail": "1 passed",
        **status_extra,
    }
    jobs.write_json(folder / "spec.json", spec)
    jobs.write_json(folder / "status.json", {"state": "done", "attempt": 1, "verification": verification})
    (folder / "result.md").write_text("done")
    return folder


def _answer(packet: dict) -> dict:
    answers = {}
    for name, question in packet["questions"].items():
        selected = "patch" if name.endswith("_evidence") else "supported"
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": {key: float(key == selected) for key in question["criteria"]},
        }
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 5, "output_tokens": 1}}


@pytest.fixture
def review(home, monkeypatch):
    from subtask import delivery_review

    calls = []
    monkeypatch.setattr(delivery_review, "_key", lambda: "test-key-not-real")
    monkeypatch.setattr(
        delivery_review, "_request", lambda packet, _: calls.append(packet) or _answer(packet)
    )
    monkeypatch.setattr(delivery_review, "calls", calls, raising=False)
    return delivery_review


def _refused(review, folder: Path) -> None:
    with pytest.raises(ValueError, match="integrity|unavailable"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert review.calls == []
    assert not list((folder / "reviews").glob("*.request.json"))


def _patch(folder: Path, report: dict) -> str:
    stored = folder / "reviews" / f"{report['request_sha256']}.request.json"
    return json.loads(stored.read_text())["state"]["materials"]["patch"]["text"]


def test_substituted_loose_blob_is_refused_before_provider(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    _overwrite(repo, _git(repo, "rev-parse", f"{head}:parser.py"), b"blob", b"value = 3\n")
    _refused(review, _job(repo, base, head))


def test_substituted_tree_that_hides_a_change_is_refused(home, review):
    """A forged subtree equal to the base side would make `git diff` print nothing."""
    repo, base, head = _repo(home, {"sub/a.txt": "1\n"}, {"sub/a.txt": "BAD\n"})
    forged = _raw(repo, "cat-file", "tree", f"{base}:sub")
    _overwrite(repo, _git(repo, "rev-parse", f"{head}:sub"), b"tree", forged)
    _refused(review, _job(repo, base, head))


def test_substituted_commit_object_is_refused(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "BAD\n"})
    forged = _raw(repo, "cat-file", "commit", head).replace(
        _git(repo, "rev-parse", f"{head}^{{tree}}").encode(),
        _git(repo, "rev-parse", f"{base}^{{tree}}").encode(),
    )
    _overwrite(repo, head, b"commit", forged)
    _refused(review, _job(repo, base, head))


def test_missing_object_is_refused(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    blob = _git(repo, "rev-parse", f"{head}:parser.py")
    (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    _refused(review, _job(repo, base, head))


def test_packed_repository_still_reviews_the_real_patch(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    _git(repo, "gc", "-q")
    folder = _job(repo, base, head)
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert "+value = 2" in _patch(folder, report)
    assert report["assessments"][0]["assessment"] == "supported"


def test_sha256_repository_is_verified(home, review):
    repo, base, head = _repo(
        home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"}, "--object-format=sha256"
    )
    assert len(head) == 64
    folder = _job(repo, base, head)
    report = review.review(DELIVERY, ["Parser fixed"])
    assert "+value = 2" in _patch(folder, report)
    _overwrite(repo, _git(repo, "rev-parse", f"{head}:parser.py"), b"blob", b"value = 3\n")
    with pytest.raises(ValueError, match="integrity"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert review.calls == []


def test_verification_reads_only_changed_material(home, review, monkeypatch):
    """Unchanged history and unchanged subtrees are never read or hashed."""
    files = {f"lib/m{i}.py": f"x = {i}\n" for i in range(200)}
    files["parser.py"] = "value = 1\n"
    repo, base, head = _repo(home, files, {"parser.py": "value = 2\n"})
    seen = []
    original = review._objects
    monkeypatch.setattr(review, "_objects", lambda r, oids, *a: seen.extend(oids) or original(r, oids, *a))
    _job(repo, base, head)
    review.review(DELIVERY, ["Parser fixed"])
    # Two commits, two root trees, two parser blobs; lib/ is skipped by its equal id.
    assert len(seen) == 6


@pytest.mark.parametrize("limit", ["MAX_GIT_OBJECTS", "MAX_GIT_OBJECT_BYTES"])
def test_verification_budget_refuses_conservatively(home, review, monkeypatch, limit):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    monkeypatch.setattr(review, limit, 3)
    _job(repo, base, head)
    with pytest.raises(ValueError, match="narrow the delivery"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert review.calls == []


def test_lfs_pointer_blocks_supported(home, review):
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:{}\nsize 12\n"
    repo, base, head = _repo(
        home, {"model.bin": pointer.format("a" * 64)}, {"model.bin": pointer.format("b" * 64)}
    )
    _job(repo, base, head)
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert "patch_content_not_inspected" in report["limitations"]
    assert report["assessments"][0]["assessment"] != "supported"


@pytest.mark.parametrize(
    "output",
    ["\x1b]0;title FAILED", "\x1b[1m1 failed\x1b[0m, 2 passed", "\x9b31mFAILED\x9b0m", "2 errors in 0.1s"],
)
def test_other_escaped_or_summary_failures_are_detected(home, review, output):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    _job(repo, base, head, output_tail=output)
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert "verification_output_conflict" in report["limitations"]


def test_coloured_success_is_not_a_failure(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    _job(repo, base, head, output_tail="\x1b[32m3 passed\x1b[0m, 0 failed")
    report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert report["limitations"] == []
    assert report["assessments"][0]["assessment"] == "supported"


def test_oracle_without_any_command_is_a_mismatch(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    _job(repo, base, head, oracle={"output_match": True, "exit_match": True})
    report = review.review(DELIVERY, ["Parser fixed"])
    assert "oracle_mismatch" in report["limitations"]


def test_non_string_repo_is_a_controlled_error(home, review):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    folder = _job(repo, base, head)
    spec = json.loads((folder / "spec.json").read_text())
    jobs.write_json(folder / "spec.json", {**spec, "repo": [str(repo)]})
    with pytest.raises(ValueError, match="must be strings"):
        review.review(DELIVERY, ["Parser fixed"])


@pytest.mark.parametrize("level", ["jobs", "reviews"])
def test_symlinked_storage_levels_are_rejected(home, review, level):
    from subtask import config

    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    folder = _job(repo, base, head)
    target = home / "elsewhere"
    if level == "jobs":
        config.JOBS.rename(target)
        config.JOBS.symlink_to(target, target_is_directory=True)
    else:
        target.mkdir()
        (folder / "reviews").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        review.review(DELIVERY, ["Parser fixed"])
    assert not list(target.rglob("*.request.json"))


def test_late_redaction_is_recorded_without_faking_the_request_digest(home, review):
    from subtask.delivery_review import LATE_REDACTION, _digest

    secret = "late-" + "z" * 16
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": f"value = '{secret}'\n"})
    folder = _job(repo, base, head)
    first = review.review(DELIVERY, ["Parser fixed"])
    stored = folder / "reviews" / f"{first['request_sha256']}.request.json"
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == first["request_sha256"]

    with (home / "env").open("a") as stream:
        stream.write("LATE_SECRET=" + secret + "\n")
    review.review(DELIVERY, ["Parser fixed"])
    record = json.loads(stored.read_text())
    marker = record.pop(LATE_REDACTION)
    assert secret not in stored.read_text()
    assert marker["request_sha256"] == first["request_sha256"]
    assert marker["stored_sha256"] == _digest(record) != first["request_sha256"]


def test_stale_receipt_of_another_binding_is_cleaned(home, review):
    secret = "older-" + "q" * 16
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    folder = _job(repo, base, head)
    reviews = folder / "reviews"
    reviews.mkdir()
    stale = reviews / f"{'0' * 64}.json"
    stale.write_text(json.dumps({"binding_sha256": "0" * 64, "model": "jev-" + secret}))
    with (home / "env").open("a") as stream:
        stream.write("OLDER_SECRET=" + secret + "\n")
    review.review(DELIVERY, ["Parser fixed"])
    assert secret not in stale.read_text()


def test_too_many_stored_records_refuse_instead_of_scanning(home, review, monkeypatch):
    repo, base, head = _repo(home, {"parser.py": "value = 1\n"}, {"parser.py": "value = 2\n"})
    folder = _job(repo, base, head)
    reviews = folder / "reviews"
    reviews.mkdir()
    for digit in "12":
        (reviews / f"{digit * 64}.request.json").write_text("{}")
    monkeypatch.setattr(review, "MAX_STORED_RECORDS", 1)
    with pytest.raises(ValueError, match="too many stored review records"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert review.calls == []
