"""Final incremental checks for the canonical-tree parser in the v6 review.

The two attacks from st-f936375bcb are covered by
``tests/test_delivery_review_postopus.py`` (red on 344c1d9, green on e676ffc).
This file pins the acceptance side of the parser change: real trees produced by
Git, including the directory/file ordering edge cases, must still be accepted.
Only temporary repositories are used; there is no network or real credential.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run(cwd: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def _git(cwd: Path, *args: str) -> str:
    return _run(cwd, *args).decode().strip()


def _entry(mode: bytes, name: bytes, width: int = 20) -> bytes:
    return mode + b" " + name + b"\0" + b"\x01" * width


def _repo(home: Path, object_format: str) -> tuple[Path, str]:
    repo = home / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", f"--object-format={object_format}")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "parser.py").write_text("value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _tricky_head(repo: Path) -> str:
    (repo / "d").mkdir()
    (repo / "d" / "inside.txt").write_text("inside\n")
    for name in ("d-", "d.", "d0"):
        (repo / name).write_text(name + "\n")
    (repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "run.sh").chmod(0o755)
    (repo / "link").symlink_to("d/inside.txt")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "tricky tree")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_real_git_tree_order_is_accepted(home, object_format):
    from subtask import delivery_review as review

    repo, base = _repo(home, object_format)
    head = _tricky_head(repo)
    width = {"sha1": 20, "sha256": 32}[object_format]
    raw = _run(repo, "cat-file", "tree", f"{head}^{{tree}}")
    parsed = review._tree(raw, width)

    listed = [name for name in _run(repo, "ls-tree", "-z", "--name-only", head).split(b"\0") if name]
    assert list(parsed) == listed
    assert set(parsed) == {b"d", b"d-", b"d.", b"d0", b"link", b"parser.py", b"run.sh"}
    assert parsed[b"d"][0] == b"40000"
    assert parsed[b"run.sh"][0] == b"100755"
    assert parsed[b"link"][0] == b"120000"

    # The whole verification path, not only the parser helper, must accept it.
    review._verify_snapshot(repo, base, head)


@pytest.mark.parametrize("width", [20, 32])
def test_all_canonical_modes_are_accepted(width):
    from subtask import delivery_review as review

    entries = [
        _entry(b"100644", b"a", width),
        _entry(b"100755", b"b", width),
        _entry(b"120000", b"c", width),
        _entry(b"40000", b"d", width),
        _entry(b"160000", b"e", width),
    ]
    parsed = review._tree(b"".join(entries), width)
    assert [mode for mode, _ in parsed.values()] == [
        b"100644",
        b"100755",
        b"120000",
        b"40000",
        b"160000",
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [_entry(b"100644", b"d0"), _entry(b"40000", b"d")],
        [_entry(b"40000", b"d"), _entry(b"100644", b"d-")],
        [_entry(b"100644", b"z"), _entry(b"100644", b"a")],
    ],
)
def test_noncanonical_directory_file_order_is_refused(entries):
    from subtask import delivery_review as review

    with pytest.raises(ValueError, match="noncanonical order"):
        review._tree(b"".join(entries), 20)


@pytest.mark.parametrize(
    ("mode", "name"),
    [
        (b"100664", b"a"),
        (b"100600", b"a"),
        (b"040000", b"d"),
        (b"100644", b"a/b"),
        (b"100644", b".git"),
        (b"100644", b".."),
        (b"100644", b""),
    ],
)
def test_illegal_tree_entries_are_refused(mode, name):
    from subtask import delivery_review as review

    with pytest.raises(ValueError, match="malformed"):
        review._tree(_entry(mode, name), 20)
