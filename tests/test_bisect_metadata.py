"""Cooperating bisects must serialize Git metadata, not their checks."""

from __future__ import annotations

import multiprocessing as mp
import subprocess
import time
from pathlib import Path

import pytest

from .test_oracle_bisect import _history

CTX = mp.get_context("fork")


def _probe(repo, commit, home, start, checking, active, maximum, counter_lock, results):
    from subtask import bisect_tool, config

    config.HOME = Path(home)
    config.HOME.mkdir()
    original_git = bisect_tool._git
    original_run = subprocess.run

    def observed_git(repository, *args):
        mutation = args[:2] in (("worktree", "add"), ("worktree", "remove"))
        if not mutation:
            return original_git(repository, *args)
        with counter_lock:
            active.value += 1
            maximum.value = max(maximum.value, active.value)
        try:
            # Widen overlap when the caller fails to serialize metadata.
            time.sleep(0.04)
            return original_git(repository, *args)
        finally:
            with counter_lock:
                active.value -= 1

    def observed_run(argv, *args, **kwargs):
        if argv[:2] == ["sh", "-c"]:
            # Both commands must enter together: holding the metadata lock
            # over a check would break this barrier instead of passing.
            checking.wait(timeout=5)
        return original_run(argv, *args, **kwargs)

    bisect_tool._git = observed_git
    subprocess.run = observed_run
    try:
        start.wait(timeout=5)
        results.put((bisect_tool._passes(repo, commit, "true"), None))
    except Exception as exc:  # noqa: BLE001 -- report a child failure to its parent
        results.put((False, repr(exc)))


@pytest.mark.parametrize("linked_repo", (False, True))
def test_metadata_serialized_across_processes_with_distinct_homes(home, linked_repo):
    repo, good, _ = _history(home, 3, 2)
    other_repo = repo
    if linked_repo:
        other_repo = home / "linked"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(other_repo), good],
            check=True,
        )
    start, checking = CTX.Barrier(2), CTX.Barrier(2)
    active, maximum = CTX.Value("i", 0), CTX.Value("i", 0)
    counter_lock, results = CTX.Lock(), CTX.Queue()
    workers = [
        CTX.Process(
            target=_probe,
            args=(
                str(repo if i == 0 else other_repo),
                good,
                str(home / f"client-{i}"),
                start,
                checking,
                active,
                maximum,
                counter_lock,
                results,
            ),
        )
        for i in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        outcomes = [results.get(timeout=15) for _ in workers]
        for worker in workers:
            worker.join(timeout=5)
        assert all(not worker.is_alive() and worker.exitcode == 0 for worker in workers)
        assert outcomes == [(True, None), (True, None)], outcomes
        assert maximum.value == 1, "Git metadata operations overlapped"
        listed = subprocess.check_output(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"], text=True
        )
        assert listed.count("worktree ") == (2 if linked_repo else 1), listed
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.kill()
            worker.join(timeout=5)
