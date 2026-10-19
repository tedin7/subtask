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

from . import ast_merge, config, jobs, verifier

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
        st = jobs.status(job_id)
        if st.get("state") not in jobs.FINAL or verifier.pending(st):
            return [], f"{job_id} is still running (or waiting for its retry)"
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
        grafted, problem = _graft(repo, scratch, found)
        if problem:
            return f"conflict: {problem}. Nothing changed."
        skip = [f":(exclude){p}" for p in grafted]
        conflicts = []
        for job_id, sp in found:
            patch = _git(repo, "diff", "--binary", f"{sp['base']}..{sp['branch']}", "--", ".", *skip).stdout
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
    # Check first, then a plain apply: on any clash the caller's tree and index stay as they were.
    done = _git(repo, "apply", "--check", "-", stdin=combined)
    if done.returncode == 0:
        done = _git(repo, "apply", "-", stdin=combined)
    if done.returncode != 0:
        return f"not applied: the working tree changed under the patches ({done.stderr.strip()[:200]})"
    names = _git(repo, "diff", "--name-only").stdout.split()
    joined = f" · merged by symbol: {', '.join(grafted)}" if grafted else ""
    return f"applied {len(found)} job(s) to {repo} (uncommitted): " + ", ".join(names[:15]) + joined


def _files(repo: str, sp: dict) -> list[str]:
    return _git(repo, "diff", "--name-only", "--no-renames", f"{sp['base']}..{sp['branch']}").stdout.split()


def _graft(repo: str, scratch: str, found: list[tuple[str, dict]]) -> tuple[list[str], str | None]:
    """Python files changed by two or more jobs that all declared symbol_scope: merged by symbol."""
    touched: dict[str, list[tuple[str, dict]]] = {}
    for job_id, sp in found:
        for path in _files(repo, sp):
            touched.setdefault(path, []).append((job_id, sp))
    grafted = []
    for path, owners in sorted(touched.items()):
        if len(owners) < 2 or not path.endswith(".py"):
            continue
        if not all(any(s.startswith(path + "::") for s in sp.get("symbol_scope") or []) for _, sp in owners):
            continue
        base = _git(scratch, "show", f"HEAD:{path}")
        if base.returncode != 0:
            return [], f"{path} is new in several jobs"
        gone = [
            j for j, sp in owners if _git(repo, "cat-file", "-e", f"{sp['branch']}:{path}").returncode != 0
        ]
        if gone:
            return [], f"{path} is deleted by {', '.join(gone)} and changed by another job"
        versions = [
            (
                j,
                _git(repo, "show", f"{sp['base']}:{path}").stdout,
                _git(repo, "show", f"{sp['branch']}:{path}").stdout,
            )
            for j, sp in owners
        ]
        try:
            merged = ast_merge.graft(base.stdout, versions)
        except ast_merge.Conflict as exc:
            return [], f"{path}: {exc}"
        with open(f"{scratch}/{path}", "w", encoding="utf-8") as fh:
            fh.write(merged)
        _git(scratch, "add", "--", path)
        grafted.append(path)
    return grafted, None


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
