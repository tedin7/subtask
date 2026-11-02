from __future__ import annotations

import os
import subprocess
import sys
import time


def _job(jobs, job_id, status, spec=None):
    folder = jobs.job_dir(job_id)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "status.json", status)
    jobs.write_json(folder / "spec.json", spec or {})
    return folder


def _snapshot(folder):
    return {
        str(path.relative_to(folder)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_gc_preview_keeps_job_store_and_git_index_unchanged(home, tmp_path):
    from subtask import config, gc, jobs

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "file").write_text("unchanged\n")
    subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@example.org",
            "commit",
            "-qm",
            "test",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "test"], check=True
    )  # the job branch must exist (T-7022)
    # An unchanged file with a new mtime forces status to refresh the index unless optional locks are disabled.
    old_mtime = (repo / "file").stat().st_mtime_ns
    os.utime(repo / "file", ns=(old_mtime + 2_000_000_000, old_mtime + 2_000_000_000))
    index = repo / ".git" / "index"
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    old = time.time() - 40 * 86400
    _job(jobs, "st-queued", {"v": 1, "state": "queued", "updated": old})
    _job(
        jobs,
        "st-dead",
        {"v": 1, "state": "running", "updated": old, "proc": {"pid": 99999999, "start": None, "boot": None}},
    )
    _job(
        jobs,
        "st-done",
        {"v": 1, "state": "done", "updated": old},
        {"branch": "test", "cwd": str(repo), "repo": str(repo)},
    )
    (config.HOME / "ledger").mkdir(parents=True)
    (config.HOME / "ledger" / "reserved.jsonl").write_text("sentinel\n")
    (config.HOME / "telemetry").mkdir()
    (config.HOME / "telemetry" / "2000-01.jsonl").write_text("sentinel\n")
    before = _snapshot(config.HOME)

    result = gc.run(days=30)

    assert "would remove 1 job folders" in result
    assert _snapshot(config.HOME) == before
    assert (index.read_bytes(), index.stat().st_mtime_ns) == index_before


def test_gc_apply_skips_stale_active_jobs(home):
    from subtask import gc, jobs

    old = time.time() - 40 * 86400
    active = _job(jobs, "st-queued", {"v": 1, "state": "queued", "updated": old})
    completed = _job(jobs, "st-done", {"v": 1, "state": "done", "updated": old})
    before = _snapshot(active)

    result = gc.run(days=30, apply=True)

    assert "removed 1 job folders" in result
    assert not completed.exists()
    assert _snapshot(active) == before


def test_gc_cli_preview_does_not_settle_stale_job(home):
    from subtask import config, jobs

    old = time.time() - 40 * 86400
    _job(jobs, "st-queued", {"v": 1, "state": "queued", "updated": old})
    _job(jobs, "st-done", {"v": 1, "state": "done", "updated": old})
    before = _snapshot(config.HOME)

    result = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "gc", "--days", "30"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "would remove 1 job folders" in result.stdout
    assert _snapshot(config.HOME) == before
