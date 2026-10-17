"""Tappa 6: invariants gate verify, allowed_paths reverts, symbol_scope merges by symbol."""

from __future__ import annotations

import subprocess

import pytest

from .conftest import wait_final

GIT = "git -c user.name=t -c user.email=t@t"
SAY = 'echo \'{"say":"## Summary\\n- ok"}\''

BASE = """import os


def alpha():
    return 1


def beta():
    return 2


class Gamma:
    def run(self):
        return 3
"""

TESTS = """def test_one():
    assert True


def test_two():
    assert True
"""


def _repo(path, files=None):
    path.mkdir()
    for name, text in (files or {}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "root"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path), *cmd], check=True
        )
    return path


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout


def _job(core, monkeypatch, repo, script, **kw):
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"{script}; {GIT} add -A; {GIT} commit -qm w; {SAY}")
    out = core.start("x", engine="fake", repo=str(repo), max_usd=0.5, **kw)
    job = out.split()[0]
    assert " started " in out, out
    wait_final(job)
    return job


# --- ast_merge, pure ---------------------------------------------------------


def test_changed_symbols_names_defs_and_module_changes():
    from subtask import ast_merge

    assert ast_merge.changed_symbols(BASE, BASE.replace("return 1", "return 10")) == {"alpha"}
    assert ast_merge.changed_symbols(BASE, BASE.replace("import os", "import sys")) == {"<module>"}
    assert ast_merge.changed_symbols(BASE, BASE + "\n\ndef delta():\n    pass\n") == {"delta"}
    assert ast_merge.changed_symbols(BASE, BASE.replace("return 3", "return 30")) == {"Gamma"}


def test_graft_merges_disjoint_symbols_and_refuses_overlaps():
    from subtask import ast_merge

    a = BASE.replace("return 1", "return 10")
    b = BASE.replace("return 2", "return 20") + "\n\ndef delta():\n    return 4\n"
    merged = ast_merge.graft(BASE, [("A", BASE, a), ("B", BASE, b)])
    assert "return 10" in merged and "return 20" in merged and "def delta" in merged
    assert "return 3" in merged and merged.startswith("import os")
    with pytest.raises(ast_merge.Conflict, match="both change alpha"):
        ast_merge.graft(BASE, [("A", BASE, a), ("B", BASE, a.replace("10", "11"))])
    with pytest.raises(ast_merge.Conflict, match="module-level"):
        ast_merge.graft(BASE, [("A", BASE, a.replace("import os", "import sys"))])
    with pytest.raises(ast_merge.Conflict, match="not valid Python"):
        ast_merge.graft(BASE, [("A", BASE, "def (:")])


def test_graft_refuses_a_symbol_changed_on_the_target_meanwhile():
    from subtask import ast_merge

    target = BASE.replace("return 1", "return 99")
    with pytest.raises(ast_merge.Conflict, match="changed on the target"):
        ast_merge.graft(target, [("A", BASE, BASE.replace("return 1", "return 10"))])


# --- validation --------------------------------------------------------------


def test_bad_arguments_are_refused(home):
    from subtask import core, integrate

    repo = _repo(home / "repo")
    assert core.start("x", engine="fake", repo=str(repo), invariants=["nope"]).startswith("invariants must")
    assert core.start("x", engine="fake", repo=str(repo), symbol_scope=["a.py:f"]).startswith("symbol_scope")
    assert core.start("x", engine="fake", repo=str(repo), allowed_paths=[]).startswith("allowed_paths")
    assert core.start("x", engine="fake", cwd=str(home), allowed_paths=["a"]).startswith(
        "allowed_paths and symbol_scope need a worktree"
    )


# --- invariants in the worker ------------------------------------------------


def test_deleting_a_test_fails_verify_without_running_it(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"tests/test_x.py": TESTS})
    script = "printf 'def test_one():\\n    assert True\\n' > tests/test_x.py; touch ran-not"
    job = _job(core, monkeypatch, repo, script, verify="touch verify-ran; true")
    v = jobs.status(job)["verification"]
    assert v["exit_code"] == 1 and v["level"] == "invariants"
    assert "no_test_deletion: tests/test_x.py has 1 test functions, base had 2" in v["invariants"]
    assert not (home / "repo").joinpath("verify-ran").exists()


def test_new_skip_marker_and_suppression_comments_are_violations(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"tests/test_x.py": TESTS, "m.py": "x = 1\n"})
    script = (
        "printf 'import pytest\\n\\n\\n@pytest.mark.skip\\n' > t; cat tests/test_x.py >> t; mv t tests/test_x.py; "
        "echo 'y = 2  # type: ignore' >> m.py"
    )
    job = _job(core, monkeypatch, repo, script, verify="true")
    found = jobs.status(job)["verification"]["invariants"]
    assert any("adds a skip/xfail marker" in f for f in found)
    assert any(f.startswith("no_suppression: m.py") for f in found)


def test_invariants_can_be_switched_off(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"tests/test_x.py": TESTS})
    job = _job(core, monkeypatch, repo, "git rm -q tests/test_x.py", verify="true", invariants=[])
    assert jobs.status(job)["verification"]["exit_code"] == 0


def test_violation_is_retried_with_the_reason(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"m.py": "x = 1\n"})
    script = (
        'if [ -f once ]; then sed -i "s/  # noqa//" m.py; else touch once; echo "z = 3  # noqa" >> m.py; fi'
    )
    job = _job(core, monkeypatch, repo, script, verify="true", max_retries=1)
    out = core.wait([job], timeout=60)
    st = jobs.status(job)
    assert st["verification"]["exit_code"] == 0, (out, st)
    assert "no_suppression: m.py" in (jobs.job_dir(job) / "reply-2.md").read_text()


def test_allowed_paths_reverts_and_commits_files_outside(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"src/a.py": "a = 1\n", "other.py": "o = 1\n"})
    script = "echo 'a = 2' > src/a.py; echo 'o = 2' > other.py; echo new > stray.txt"
    job = _job(core, monkeypatch, repo, script, verify="true", allowed_paths=["src"])
    sp, v = jobs.spec(job), jobs.status(job)["verification"]
    assert v["exit_code"] == 0 and sorted(v["reverted"]) == ["other.py", "stray.txt"]
    changed = _git(repo, "diff", "--name-only", f"{sp['base']}..{sp['branch']}").split()
    assert changed == ["src/a.py"]


def test_symbol_scope_blocks_changes_to_other_symbols(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"m.py": BASE})
    script = "sed -i 's/return 1/return 10/; s/return 2/return 20/' m.py"
    job = _job(core, monkeypatch, repo, script, verify="true", symbol_scope=["m.py::alpha"])
    found = jobs.status(job)["verification"]["invariants"]
    assert found == ["symbol_scope: m.py changes beta outside ['alpha']"]


# --- integrate by symbol -----------------------------------------------------


def test_integrate_merges_two_jobs_editing_one_file_by_symbol(home, monkeypatch):
    from subtask import core, integrate

    repo = _repo(home / "repo", {"m.py": BASE, "n.txt": "n\n"})
    a = _job(core, monkeypatch, repo, "sed -i 's/return 1/return 10/' m.py", symbol_scope=["m.py::alpha"])
    b = _job(
        core, monkeypatch, repo, "sed -i 's/return 2/return 20/' m.py; echo x >> n.txt",
        symbol_scope=["m.py::beta"],
    )  # fmt: skip
    out = integrate.integrate([a, b], "apply")
    assert out.startswith("applied 2 job(s)") and "merged by symbol: m.py" in out, out
    text = (repo / "m.py").read_text()
    assert "return 10" in text and "return 20" in text and "return 3" in text
    assert (repo / "n.txt").read_text() == "n\nx\n"


def test_integrate_by_symbol_refuses_overlap_and_changes_nothing(home, monkeypatch):
    from subtask import core, integrate

    repo = _repo(home / "repo", {"m.py": BASE})
    a = _job(core, monkeypatch, repo, "sed -i 's/return 1/return 10/' m.py", symbol_scope=["m.py::alpha"])
    b = _job(core, monkeypatch, repo, "sed -i 's/return 1/return 11/' m.py", symbol_scope=["m.py::alpha"])
    out = integrate.integrate([a, b], "apply")
    assert out.startswith("conflict: m.py:") and "both change alpha" in out, out
    assert (repo / "m.py").read_text() == BASE
