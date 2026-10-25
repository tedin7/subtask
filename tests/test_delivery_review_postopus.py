"""Post-fix adversarial checks for the v6 Git object verification.

Only temporary repositories and a fake provider are used: no network and no
real credentials.  Both cases below are concrete gaps left by
aafedf3be628e9d21414c5cd08eebba713078d9e, not the same-UID race documented in
docs/delivery-review.md.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import zlib
from pathlib import Path

import pytest

from subtask import jobs

DELIVERY = "st-postopus"


@pytest.fixture(autouse=True)
def _fresh_redaction_set():
    from subtask import redact

    redact._known.clear()
    yield
    redact._known.clear()


def _run(cwd: Path, *args: str, data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=data,
        capture_output=True,
        check=check,
        timeout=30,
    )


def _git(cwd: Path, *args: str, data: bytes | None = None) -> str:
    return _run(cwd, *args, data=data).stdout.decode().strip()


def _repo(home: Path) -> tuple[Path, str]:
    repo = home / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "parser.py").write_text("value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")
    return repo, _git(repo, "rev-parse", "HEAD")


def _head(repo: Path, content: str) -> str:
    (repo / "parser.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    return _git(repo, "rev-parse", "HEAD")


def _job(repo: Path, base: str, head: str) -> Path:
    folder = jobs.job_dir(DELIVERY)
    folder.mkdir(parents=True, exist_ok=True)
    spec = {
        "task": "Fix parser",
        "engine": "fake",
        "repo": str(repo),
        "cwd": str(repo),
        "branch": _git(repo, "symbolic-ref", "--short", "HEAD"),
        "base": base,
        "verify": "pytest -q",
    }
    verification = {
        "exit_code": 0,
        "head": head,
        "dirty_after": False,
        "command": "pytest -q",
        "output_tail": "1 passed",
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


def _corrupt_blob(repo: Path, oid: str, content: bytes) -> None:
    path = repo / ".git" / "objects" / oid[:2] / oid[2:]
    assert path.is_file(), "the fresh commit must use a loose object"
    path.chmod(0o644)
    path.write_bytes(zlib.compress(b"blob %d\0" % len(content) + content))


def _redirect_commit_graph(repo: Path, commit: str, tree: str) -> None:
    """Point the commit-graph entry for `commit` at another (valid) tree."""
    graph = repo / ".git" / "objects" / "info" / "commit-graph"
    raw = bytearray(graph.read_bytes())
    assert raw[:4] == b"CGPH"
    assert raw[5] == 1, "this helper only supports SHA-1 commit-graphs"
    position = 8
    chunks = {}
    for _ in range(raw[6]):
        name = bytes(raw[position : position + 4])
        chunks[name] = int.from_bytes(raw[position + 4 : position + 12], "big")
        position += 12
    fanout, lookup, data = chunks[b"OIDF"], chunks[b"OIDL"], chunks[b"CDAT"]
    count = int.from_bytes(raw[fanout + 255 * 4 : fanout + 256 * 4], "big")
    width = 20
    oids = [bytes(raw[lookup + i * width : lookup + (i + 1) * width]).hex() for i in range(count)]
    index = oids.index(commit)
    following = min(offset for offset in chunks.values() if offset > data)
    entry_size = (following - data) // count
    start = data + index * entry_size
    raw[start : start + width] = bytes.fromhex(tree)
    raw[-width:] = hashlib.sha1(bytes(raw[:-width])).digest()
    graph.chmod(0o644)
    graph.write_bytes(raw)
    assert _run(repo, "commit-graph", "verify", check=False).returncode != 0


def test_duplicate_tree_entry_cannot_hide_a_substituted_blob(home, monkeypatch):
    """`_tree` keeps only the last of two equal names; `git diff` reads both."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base = _repo(home)
    real = _git(repo, "hash-object", "-w", "--stdin", data=b"value = 2\n")
    same = _git(repo, "hash-object", "-w", "--stdin", data=b"value = 1\n")

    def entry(oid: str) -> bytes:
        return b"100644 parser.py\0" + bytes.fromhex(oid)

    tree = _git(
        repo,
        "hash-object",
        "--literally",
        "-t",
        "tree",
        "-w",
        "--stdin",
        data=entry(real) + entry(same),
    )
    head = _git(repo, "commit-tree", tree, "-m", "duplicate")
    branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "update-ref", f"refs/heads/{branch}", head)
    _corrupt_blob(repo, real, b"value = 3\n")

    folder = _job(repo, base, head)
    calls = []
    monkeypatch.setattr(review, "_request", lambda packet, _: calls.append(packet) or _answer(packet))
    with pytest.raises(ValueError, match="integrity|malformed|unavailable|changed objects"):
        review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    assert calls == []
    assert not list((folder / "reviews").glob("*.request.json"))


def test_commit_graph_cannot_redirect_the_diff_to_unverified_objects(home, monkeypatch):
    """A valid commit-graph is trusted by `git diff` but not checked by `_verify_snapshot`."""
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_key", lambda: "test-key-not-real")
    repo, base = _repo(home)
    head = _head(repo, "value = 2\n")
    fake_blob = _git(repo, "hash-object", "-w", "--stdin", data=b"value = 3\n")
    fake_tree = _git(repo, "mktree", data=b"100644 blob " + fake_blob.encode() + b"\tparser.py\n")
    _git(repo, "commit-graph", "write", "--reachable")
    _redirect_commit_graph(repo, head, fake_tree)
    _corrupt_blob(repo, fake_blob, b"value = 4\n")

    folder = _job(repo, base, head)
    calls = []
    monkeypatch.setattr(review, "_request", lambda packet, _: calls.append(packet) or _answer(packet))
    try:
        report = review.review(DELIVERY, ["Parser fixed"], submit=True, original_request="Fix parser")
    except ValueError:
        assert calls == []
        assert not list((folder / "reviews").glob("*.request.json"))
        return
    stored = folder / "reviews" / f"{report['request_sha256']}.request.json"
    patch = json.loads(stored.read_text())["state"]["materials"]["patch"]["text"]
    assert "value = 2" in patch
    assert "value = 4" not in patch
    assert all("value = 4" not in json.dumps(packet) for packet in calls)
