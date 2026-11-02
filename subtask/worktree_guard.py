"""Why a job worktree must not be removed yet (T-7022): None means it is safe to remove.

A worktree is kept when it has commits that no other ref holds, or when any Git check
fails. The reason is always returned as text so the caller can show it; an unreadable
state is never treated as clean.
"""

from __future__ import annotations

import subprocess


def _run(cwd: str, *args: str) -> tuple[subprocess.CompletedProcess | None, str]:
    try:
        done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git {args[0]} failed to run: {exc}"
    if done.returncode != 0:
        return None, f"git {args[0]} failed: {(done.stderr or done.stdout).strip()[:200]}"
    return done, ""


def unique_commits(sp: dict) -> tuple[int | None, str]:
    """Commits of the job branch that no other branch or remote ref contains."""
    branch = sp.get("branch")
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    others = ["--exclude=" + branch] if branch else []
    done, err = _run(sp["cwd"], "rev-list", "--count", ref, "--not", *others, "--branches", "--remotes")
    if done is None:
        return None, err
    try:
        return int(done.stdout.strip()), ""
    except ValueError:
        return None, f"git rev-list gave an unreadable answer: {done.stdout.strip()[:80]}"


def removal_blocker(sp: dict) -> str | None:
    count, err = unique_commits(sp)
    if count is None:
        return f"check failed, worktree kept ({err})"
    if count:
        return (
            f"worktree has {count} commit(s) found on no other branch, not removed; merge the branch, "
            f"or discard the job with integrate(action='discard')"
        )
    return None
