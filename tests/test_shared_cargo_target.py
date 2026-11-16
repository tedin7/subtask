"""Rust worktrees share one CARGO_TARGET_DIR per repository instead of a target/ each."""

from subtask import config, worker


def _spec(tmp_path, cargo=True):
    repo = tmp_path / "arvx"
    cwd = tmp_path / "wt" / "st-1"
    repo.mkdir()
    cwd.mkdir(parents=True)
    if cargo:
        (cwd / "Cargo.toml").write_text("[workspace]\n")
    return {"repo": str(repo), "cwd": str(cwd)}


def test_worktree_level_rust_repo_gets_shared_target(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.delenv("SUBTASK_SHARED_CARGO_TARGET", raising=False)
    spec = _spec(tmp_path)
    first = worker._shared_cargo_target(spec, "worktree")["CARGO_TARGET_DIR"]
    other = dict(spec, cwd=spec["cwd"])
    assert worker._shared_cargo_target(other, "worktree")["CARGO_TARGET_DIR"] == first
    assert first.startswith(str(tmp_path / "home" / "cargo-target" / "arvx-"))
    assert not first.startswith(spec["cwd"])


def test_no_shared_target_when_sandboxed_not_rust_or_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.delenv("SUBTASK_SHARED_CARGO_TARGET", raising=False)
    spec = _spec(tmp_path)
    assert worker._shared_cargo_target(spec, "sandbox") == {}
    assert worker._shared_cargo_target(spec, "isolated") == {}
    assert worker._shared_cargo_target({"cwd": spec["cwd"]}, "worktree") == {}
    monkeypatch.setenv("SUBTASK_SHARED_CARGO_TARGET", "0")
    assert worker._shared_cargo_target(spec, "worktree") == {}


def test_plain_repo_has_no_cargo_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    assert worker._shared_cargo_target(_spec(tmp_path, cargo=False), "worktree") == {}


def test_isolated_vars_carry_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    monkeypatch.delenv("SUBTASK_SHARED_CARGO_TARGET", raising=False)
    folder = tmp_path / "job"
    folder.mkdir()
    out = worker._isolated_vars("st-1", _spec(tmp_path), folder, "worktree")
    assert "CARGO_TARGET_DIR" in out and "TMPDIR" in out
