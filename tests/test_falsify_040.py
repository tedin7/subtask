"""Adversarial tests for subtask 0.4.0: each one fails on the reviewed code."""

from __future__ import annotations

import subprocess
from unittest import mock

from subtask import ast_merge, invariants

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _repo(path, files):
    path.mkdir(parents=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "root"]):
        subprocess.run([*GIT, "-C", str(path), *cmd], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_form_feed_breaks_line_slicing():
    """ast_merge.py:30 text.splitlines() splits on \\x0c but ast linenos do not: a file with a form feed
    line makes _parts slice the wrong lines, so graft produces a corrupt or wrong merge."""
    base = "def a():\n    return 1\n\x0c\ndef b():\n    return 2\n"
    new_b = base.replace("return 2", "return 22")
    merged = ast_merge.graft(base, [("j", base, new_b)])
    assert merged == new_b


def test_duplicate_toplevel_names_hide_changes():
    """ast_merge.py:31 _parts keys defs by name, so the first of two same-named top-level defs
    (singledispatch `_`) is invisible: changed_symbols() is empty although the source changed
    (graft would drop the edit, symbol_scope would not see it)."""
    old = "def _(x):\n    return 1\n\n\ndef _(y):\n    return 2\n"
    new = old.replace("return 1", "return 99")
    assert ast_merge.changed_symbols(old, new) != set()


def test_comment_only_change_is_silently_dropped_by_graft():
    """ast_merge.py:60/92 comments between symbols belong to no symbol: a job whose only change is a
    comment is merged away with no Conflict (silent data loss; integrate then excludes the file's patch)."""
    base = "def f():\n    return 1\n\n\n# about g\ndef g():\n    return 2\n"
    job_a = base.replace("return 1", "return 11")
    job_b = base.replace("# about g", "# about g, rewritten by job b")
    try:
        merged = ast_merge.graft(base, [("a", base, job_a), ("b", base, job_b)])
    except ast_merge.Conflict:
        return
    assert "rewritten by job b" in merged


def test_new_symbol_lands_after_main_guard():
    """ast_merge.py:97 new symbols are appended at EOF, after `if __name__ == '__main__':`; a function
    used at runtime by the main block is defined too late (NameError when the merged file runs)."""
    base = "def f():\n    return 1\n\n\nif __name__ == '__main__':\n    print(f())\n"
    a = base.replace("return 1", "return h()")
    a = a.replace("\n\nif __name__", "\n\ndef h():\n    return 5\n\n\nif __name__")
    b = base.replace("\n\nif __name__", "\n\ndef g():\n    return 0\n\n\nif __name__")
    merged = ast_merge.graft(base, [("a", base, a), ("b", base, b)])
    exec(compile(merged, "m.py", "exec"), {"__name__": "__main__"})  # noqa: S102 - NameError: h defined after main


def test_unicode_test_file_deletion_not_detected(tmp_path):
    """invariants.py:67 git quotes non-ASCII paths in --name-status, so the quoted name is used to
    read old/new text: deleting the tests in tests/test_café.py reports no violation."""
    repo = tmp_path / "r"
    name = "tests/test_café.py"
    base = _repo(repo, {name: "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 1\n"})
    (repo / name).unlink()
    assert invariants.check(str(repo), base, ["no_test_deletion"]) != []


def test_importorskip_not_seen_as_skip(tmp_path):
    """invariants.py:30 SKIP misses pytest.importorskip(...) at module level, which skips a whole test file."""
    repo = tmp_path / "r"
    base = _repo(repo, {"tests/test_a.py": "def test_a():\n    assert 1\n"})
    (repo / "tests" / "test_a.py").write_text(
        'import pytest\npytest.importorskip("no_such_module_xyz")\n\n\ndef test_a():\n    assert 1\n'
    )
    assert invariants.check(str(repo), base, ["no_test_deletion"]) != []


def test_root_conftest_skip_not_seen(tmp_path):
    """invariants.py:75 _is_test ignores a root-level conftest.py, so a collection hook that skips
    every test passes no_test_deletion."""
    repo = tmp_path / "r"
    base = _repo(repo, {"conftest.py": "", "tests/test_a.py": "def test_a():\n    assert 1\n"})
    (repo / "conftest.py").write_text(
        "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
        "    for i in items:\n        i.add_marker(pytest.mark.skip)\n"
    )
    assert invariants.check(str(repo), base, ["no_test_deletion"]) != []


def test_allowed_path_with_dot_slash_reverts_allowed_files(tmp_path):
    """invariants.py:107 _allowed never normalises './src': every file under src/ counts as outside
    allowed_paths and is reverted, wiping the job's legitimate work."""
    repo = tmp_path / "r"
    base = _repo(repo, {"src/a.py": "x = 1\n"})
    (repo / "src" / "a.py").write_text("x = 2\n")
    assert invariants.revert_outside(str(repo), base, ["./src"]) == []
    assert (repo / "src" / "a.py").read_text() == "x = 2\n"


def test_unicode_file_outside_allowed_paths_not_reverted(tmp_path):
    """invariants.py:67/116 quoted non-ASCII path: `git checkout base -- "caf\\303\\251.txt"` fails
    silently, so the file outside allowed_paths stays modified."""
    repo = tmp_path / "r"
    name = "café.txt"
    base = _repo(repo, {"src/a.py": "x = 1\n", name: "old\n"})
    (repo / name).write_text("hacked\n")
    invariants.revert_outside(str(repo), base, ["src"])
    assert (repo / name).read_text() == "old\n"


def test_bisect_passing_bad_ref_names_a_culprit(home, tmp_path):
    """bisect_tool.py:99 `hi` starts at the last commit and is never tested: when the check passes on
    every commit (bad_ref is not actually bad) find() still returns bad_ref as the first bad commit."""
    from subtask import bisect_tool

    repo = tmp_path / "r"
    good = _repo(repo, {"f": "1\n"})
    for i in range(3):
        (repo / "f").write_text(f"{i + 2}\n")
        subprocess.run([*GIT, "-C", str(repo), "commit", "-qam", f"c{i}"], check=True)
    found = bisect_tool.find(str(repo), good, "HEAD", "true", 2)
    assert "commit" not in found, found


def test_chained_foreach_is_not_expanded():
    """groups.py:238 a foreach whose parent is itself a foreach alias is renamed away before the
    lookup: C keeps foreach='B', never expands, and later runs once with a literal {{item}}."""
    from subtask import groups

    def row(alias, deps, foreach, state, job=None):
        return {
            "alias": alias,
            "args": {"task": alias + " {{item}}"},
            "depends_on": deps,
            "foreach": foreach,
            "state": state,
            "job": job,
        }

    rows = {
        "A": row("A", [], None, "started", "ja"),
        "B": row("B", ["A"], "A", "pending"),
        "C": row("C", ["B"], "B", "pending"),
    }
    with mock.patch.object(groups, "_fanout", return_value=(["x", "y"], "")):
        out = groups._expand(rows, {"A": True})
    assert "C" not in out or out["C"].get("foreach") is None
