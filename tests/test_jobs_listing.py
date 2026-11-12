"""Job listing order: all_jobs() and newest_jobs() read the same folders the same way."""

import os


def _job(jobs_dir, name, mtime):
    folder = jobs_dir / name
    folder.mkdir(parents=True)
    os.utime(folder, (mtime, mtime))


def test_newest_jobs_matches_all_jobs_prefix(home):
    from subtask import config, jobs

    for i, name in enumerate(["st-b", "st-a", "st-c", "st-d", "st-e"]):
        _job(config.JOBS, name, 1_000_000 + (i % 3))  # ties on mtime break on the id, newest first
    (config.JOBS / "not-a-job").mkdir()
    os.symlink(config.JOBS / "missing", config.JOBS / "st-dangling")

    ordered = jobs.all_jobs()
    assert ordered == ["st-c", "st-e", "st-a", "st-d", "st-b"]
    for limit in range(len(ordered) + 2):
        assert jobs.newest_jobs(limit) == ordered[:limit]


def test_listing_without_jobs_folder(home):
    from subtask import config, jobs

    assert not config.JOBS.exists() or not any(config.JOBS.iterdir())
    if config.JOBS.exists():
        config.JOBS.rmdir()
    assert jobs.all_jobs() == []
    assert jobs.newest_jobs(8) == []
