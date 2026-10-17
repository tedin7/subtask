"""Bring finished worktree jobs back: show their diff, apply them, or throw them away.

apply works in a scratch worktree first: every patch must apply there, in
order, before anything touches the caller's working tree. A conflict changes
nothing; with auto_resolve it starts one reconciliation job instead, in its
own worktree, that merges the branches and runs every job's verify.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess

from . import config, jobs

ACTIONS = ("diff", "apply", "discard")
MAX_DIFF = 30000

RECONCILE_TASK = (
    "Merge these branches into your branch, in order: {branches}. Run `git merge --no-edit <branch>` "
    "for each. Where they conflict, keep the intent of both changes: {intents}. Commit the merge, run "
    "the checks, and finish with a ## Summary of how each conflict was resolved."
)


def _git(cwd: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args], input=stdin, capture_output=True, text=True, timeout=120, check=False
    )


def _jobs(ids: list[str]) -> tuple[list[tuple[str, dict]], str | None]:
    from .core import known

    if not isinstance(ids, list) or not ids:
        return [], "no job ids"
    out = []
    for job_id in ids:
        if not known(job_id):
            return [], f"unknown job: {job_id}"
        if jobs.status(job_id).get("state") not in jobs.FINAL:
            return [], f"{job_id} is still running"
        sp = jobs.spec(job_id)
        if not (sp.get("branch") and sp.get("repo")):
            return [], f"{job_id} has no worktree branch"
        out.append((job_id, sp))
    if len({sp["repo"] for _, sp in out}) > 1:
        return [], "jobs belong to different repositories"
    return out, None


def integrate(ids: list[str], action: str = "apply", auto_resolve: bool = False) -> str:
    if action not in ACTIONS:
        return "action must be " + "|".join(ACTIONS)
    found, problem = _jobs(ids)
    if problem:
        return problem
    if action == "diff":
        parts = [
            f"## {j}\n" + _git(sp["repo"], "diff", f"{sp['base']}..{sp['branch']}").stdout for j, sp in found
        ]
        text = "\n".join(parts)
        return text[:MAX_DIFF] + ("\n… diff cut" if len(text) > MAX_DIFF else "")
    if action == "discard":
        return "\n".join(_discard(j, sp) for j, sp in found)
    return _apply(found, auto_resolve)


def _discard(job_id: str, sp: dict) -> str:
    _git(sp["repo"], "worktree", "remove", "--force", sp["cwd"])
    gone = _git(sp["repo"], "branch", "-D", sp["branch"]).returncode == 0
    return f"{job_id}: worktree and branch removed" if gone else f"{job_id}: worktree removed, branch kept"


def _apply(found: list[tuple[str, dict]], auto_resolve: bool) -> str:
    repo = found[0][1]["repo"]
    scratch = str(config.HOME / "integrate" / secrets.token_hex(4))
    made = _git(repo, "worktree", "add", "-q", "--detach", scratch, "HEAD")
    if made.returncode != 0:
        return f"scratch worktree not created: {made.stderr.strip()[:200]}"
    try:
        conflicts = []
        for job_id, sp in found:
            patch = _git(repo, "diff", "--binary", f"{sp['base']}..{sp['branch']}").stdout
            if not patch.strip():
                continue
            done = _git(scratch, "apply", "--3way", "--index", "-", stdin=patch)
            if done.returncode != 0:
                conflicts.append(job_id)
                _git(scratch, "reset", "-q", "--hard")
                _git(scratch, "clean", "-qfd")
                break
        if conflicts:
            return _conflict(found, conflicts, auto_resolve)
        combined = _git(scratch, "diff", "--cached", "--binary", "HEAD").stdout
    finally:
        _git(repo, "worktree", "remove", "--force", scratch)
        shutil.rmtree(scratch, ignore_errors=True)
    if not combined.strip():
        return "nothing to apply"
    done = _git(repo, "apply", "--3way", "-", stdin=combined)
    if done.returncode != 0:
        return f"not applied: the working tree changed under the patches ({done.stderr.strip()[:200]})"
    names = _git(repo, "diff", "--name-only").stdout.split()
    return f"applied {len(found)} job(s) to {repo} (uncommitted): " + ", ".join(names[:15])


def _conflict(found: list[tuple[str, dict]], conflicts: list[str], auto_resolve: bool) -> str:
    if not auto_resolve:
        return f"conflict applying {', '.join(conflicts)} after the others: nothing changed. " + (
            "Pass auto_resolve=True to start a reconciliation job."
        )
    from .core import start

    sp0 = found[0][1]
    checks = [sp["verify"] for _, sp in found if sp.get("verify")]
    task = RECONCILE_TASK.format(
        branches=", ".join(sp["branch"] for _, sp in found),
        intents="; ".join(f"{sp['branch']}: {sp['task'].splitlines()[0][:120]}" for _, sp in found),
    )
    out = start(
        task, sp0["engine"], sp0["repo"], base="HEAD", max_usd=sp0.get("max_usd"),
        verify=" && ".join(f"({c})" for c in checks) or None,
    )  # fmt: skip
    return (
        f"conflict in {', '.join(conflicts)}: reconciliation job {out}\nintegrate([its id]) when it is done"
    )
