"""Short reviews: a review job gets the branch diff and few-step rules in its brief.

Measured on 2026-11-15 (42 DeepSeek Flash reviews): median 12 minutes and 46 tool steps, because the brief only
said "look at git diff" and the model explored the repository one small command at a time. The same review with
the diff in the brief took 7 minutes and 20 steps, and found a critical defect the long one missed.
"""

from __future__ import annotations

import subprocess

MAX_DIFF = 120_000
MAX_NOTE = 4_000
MARK = "<subtask_review_diff>"
RULES = """How to review, to finish early:
1. The diff of the change is below, with the whole function around each hunk. Read all of it and list the suspected
   defects BEFORE opening anything else. Do not re-read the diff with git (except files a TRUNCATED note lists).
2. Open other files only to confirm or rule out a precise suspicion (a caller, a definition).
3. Batch: one shell command with several grep or sed calls beats many small commands.
4. Do not wander around the repository. Run a test only to confirm a precise suspicion.
5. Answer as soon as the suspicions are checked."""


def _git(root: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _upstream(root: str) -> str | None:
    """The default branch to compare with: origin/HEAD, then origin/main, origin/master, main, master."""
    head = (_git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD") or "").strip()
    for ref in [head] if head else []:
        return ref
    for ref in ("origin/main", "origin/master", "main", "master"):
        if _git(root, "rev-parse", "--verify", "-q", ref + "^{commit}") is not None:
            return ref
    return None


def _cut(diff: str) -> str:
    """Whole files up to MAX_DIFF; the others are listed in a note the reviewer must act on."""
    if len(diff) <= MAX_DIFF:
        return diff
    kept, skipped, size = [], [], 0
    for piece in ("\n" + diff).split("\ndiff --git ")[1:]:
        text = "diff --git " + piece + "\n"
        if size + len(text) <= MAX_DIFF:
            kept.append(text)
            size += len(text)
        else:
            skipped.append(piece.split("\n", 1)[0])
    note = "\n".join(skipped)[:MAX_NOTE]
    return "".join(kept) + (
        "\n[TRUNCATED: these files are NOT above. Read their diff before the verdict; without them the verdict "
        "is 'defects']\n" + note
    )


def augment(task: str, root: str | None, target: str | None) -> str:
    """The review brief with rules and the diff (upstream...target). Never raises; unchanged when no root."""
    if not root or MARK in task:
        return task
    if "diff --git " in task:
        return task + "\n\n" + RULES
    upstream = _upstream(root)
    target = target or "HEAD"
    diff = _git(root, "diff", "-W", f"{upstream}...{target}") if upstream else None
    if not diff or not diff.strip():
        return task
    stat = (_git(root, "diff", "--stat", f"{upstream}...{target}") or "")[-MAX_NOTE:]
    return (
        f"{task}\n\n{RULES}\n\n{MARK}\nChange {upstream}...{target}. Files:\n{stat}\n{_cut(diff)}\n"
        "</subtask_review_diff>"
    )
