import subprocess

from subtask import review_brief


def _repo(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], capture_output=True, text=True, check=True
        ).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "x@y")
    git("config", "user.name", "x")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    git("add", ".")
    git("commit", "-qm", "a")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-qb", "work")
    (tmp_path / "a.py").write_text("def f():\n    return 2\n")
    git("commit", "-qam", "b")
    return git


def test_review_brief_gets_rules_and_diff(tmp_path):
    _repo(tmp_path)
    out = review_brief.augment("find bugs", str(tmp_path), None)
    assert out.startswith("find bugs") and "+    return 2" in out and "How to review" in out


def test_brief_that_already_has_a_diff_gets_only_rules(tmp_path):
    _repo(tmp_path)
    out = review_brief.augment("x\ndiff --git a/a.py b/a.py\n", str(tmp_path), None)
    assert "How to review" in out and review_brief.MARK not in out


def test_no_change_or_no_repo_leaves_the_task_alone(tmp_path):
    git = _repo(tmp_path)
    git("checkout", "-q", "main")
    assert review_brief.augment("t", str(tmp_path), None) == "t"
    assert review_brief.augment("t", None, None) == "t"
    assert review_brief.augment("t", str(tmp_path / "missing"), None) == "t"


def test_target_sha_is_used(tmp_path):
    git = _repo(tmp_path)
    sha = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "main")
    assert "+    return 2" in review_brief.augment("t", str(tmp_path), sha)


def test_long_diff_is_cut_by_file_with_a_note(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(review_brief, "MAX_DIFF", 10)
    out = review_brief.augment("t", str(tmp_path), None)
    assert "[TRUNCATED" not in out and "cut inside the first file" in out  # one file: kept, cut


def test_cut_keeps_whole_files_and_lists_the_rest(monkeypatch):
    monkeypatch.setattr(review_brief, "MAX_DIFF", 60)
    diff = "diff --git a/x b/x\n+1\n" + "diff --git a/y b/y\n" + "+2\n" * 50
    out = review_brief._cut(diff)
    assert (
        "a/x b/x" in out and "TRUNCATED" in out and "[TRUNCATED" in out and out.rstrip().endswith("a/y b/y")
    )


def test_no_remote_means_no_guess(tmp_path):
    git = _repo(tmp_path)
    git("update-ref", "-d", "refs/remotes/origin/main")
    assert review_brief._upstream(str(tmp_path)) is None
