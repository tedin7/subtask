"""K-ary parallel bisect: test up to K commits at once to find the first one that breaks a check.

The commits between good (passes) and bad (fails) are taken in first-parent
order. Each round tests K evenly spaced commits in temporary worktrees, at the
same time, and keeps the interval between the last pass and the first failure.
Rounds needed: about log_(K+1) of the commit count. Worktrees are always removed.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import config

TIMEOUT_S = 900
MAX_COMMITS = 5000
MAX_DIFF = 12000


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, timeout=120, check=False
    )


def _passes(repo: str, commit: str, cmd: str) -> bool:
    scratch = str(config.HOME / "bisect" / secrets.token_hex(4))
    try:
        if _git(repo, "worktree", "add", "-q", "--detach", scratch, commit).returncode != 0:
            raise RuntimeError(f"worktree for {commit[:9]} not created")
        try:
            done = subprocess.run(
                ["sh", "-c", cmd], cwd=scratch, capture_output=True, timeout=TIMEOUT_S, check=False,
                stdin=subprocess.DEVNULL,
            )  # fmt: skip
            return done.returncode == 0
        except subprocess.TimeoutExpired:
            return False
    finally:
        _git(repo, "worktree", "remove", "--force", scratch)
        shutil.rmtree(scratch, ignore_errors=True)


def find(repo: str, good: str, bad: str, cmd: str, parallelism: int = 4) -> dict:
    """{'commit', 'rounds', 'tested'} or {'error'}."""
    resolved = []
    for ref in (good, bad):
        if ref.startswith("-"):
            return {"error": f"invalid ref: {ref}"}
        done = _git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        if done.returncode != 0:
            return {"error": f"unknown ref: {ref}"}
        resolved.append(done.stdout.strip())
    good_sha, bad_sha = resolved
    if _git(repo, "merge-base", "--is-ancestor", good_sha, bad_sha).returncode != 0:
        return {"error": "good_ref is not an ancestor of bad_ref"}
    commits = _git(repo, "rev-list", "--first-parent", "--reverse", f"{good_sha}..{bad_sha}").stdout.split()
    if not commits or len(commits) > MAX_COMMITS:
        return {"error": f"{len(commits)} commits between good and bad (1 to {MAX_COMMITS} allowed)"}
    k = max(1, min(int(parallelism), 8))
    lo, hi = -1, len(commits) - 1  # commits[lo] passes (-1 = good), commits[hi] fails (bad)
    rounds, tested = 0, 0
    with ThreadPoolExecutor(k) as pool:
        while hi - lo > 1:
            span = hi - lo - 1
            picks = sorted({lo + 1 + (span * (i + 1)) // (k + 1) for i in range(min(k, span))})
            results = dict(zip(picks, pool.map(lambda i: _passes(repo, commits[i], cmd), picks)))
            rounds, tested = rounds + 1, tested + len(picks)
            for i in picks:  # the interval ends at the first failing pick
                if results[i]:
                    lo = i
                else:
                    hi = i
                    break
    return {"commit": commits[hi], "rounds": rounds, "tested": tested, "candidates": len(commits)}


def report(repo: str, found: dict) -> str:
    if found.get("error"):
        return found["error"]
    sha = found["commit"]
    meta = _git(repo, "show", "-s", "--format=%H%n%an <%ae>%n%ad%n%s", sha).stdout.strip()
    diff = _git(repo, "show", "--stat", "--patch", "--format=", sha).stdout
    diff = diff[:MAX_DIFF] + ("\n… diff cut" if len(diff) > MAX_DIFF else "")
    head = (
        f"first bad commit: {meta}\n"
        f"{found['candidates']} candidates, {found['tested']} tested in {found['rounds']} parallel rounds"
    )
    return head + "\n\n" + diff


FIX_TASK = (
    "Commit {sha} made `{cmd}` fail. Fix the regression without undoing what that commit was for. "
    "Run the check, commit, and finish with a ## Summary.\n\nThe commit:\n{diff}"
)
