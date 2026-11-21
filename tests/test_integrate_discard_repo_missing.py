"""A review-only job whose repo (a worktree) was removed by hand is discarded, not left on «worktree not removed»."""

from subtask import integrate


def test_discard_with_missing_repo_discards_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(integrate.events, "emit", lambda *a, **k: {"msg": a[2]})
    out = integrate._discard(
        "st-x", {"repo": str(tmp_path / "gone"), "cwd": str(tmp_path / "gone"), "branch": "subtask/st-x"}
    )
    assert "job discarded" in out and "not removed" not in out
