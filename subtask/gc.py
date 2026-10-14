"""Retention and disk usage: report first, delete only with --apply, never user work.

Removable: folders of finished jobs older than the retention, whose worktree is
already gone or clean (no uncommitted and no ignored files), telemetry older
than its retention. Never removed: active jobs, dirty worktrees, branches.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config, jobs, telemetry

RETENTION_DAYS = int(os.environ.get("SUBTASK_JOB_RETENTION_DAYS", "30"))


def _size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def du() -> dict:
    parts = {
        "jobs": config.JOBS,
        "worktrees": config.WORKTREES,
        "ledger": config.HOME / "ledger",
        "telemetry": config.HOME / "telemetry",
    }
    return {name: _size(path) if path.exists() else 0 for name, path in parts.items()}


def _worktree_clean(path: str) -> bool | None:
    if not Path(path).exists():
        return None
    done = subprocess.run(["git", "-C", path, "status", "--porcelain", "--ignored"], capture_output=True, text=True, check=False)
    return done.returncode == 0 and not done.stdout.strip()


def plan(days: int = RETENTION_DAYS) -> list[dict]:
    cutoff = time.time() - days * 86400
    out = []
    for job_id in jobs.all_jobs():
        folder = jobs.job_dir(job_id)
        st, sp = jobs.status(job_id), jobs.spec(job_id)
        if st.get("state") not in jobs.FINAL:
            continue
        if float(st.get("updated") or folder.stat().st_mtime) > cutoff:
            continue
        item = {"job": job_id, "folder": str(folder), "bytes": _size(folder), "worktree": None}
        if sp.get("branch") and sp.get("cwd"):
            clean = _worktree_clean(sp["cwd"])
            if clean is False:
                item["skip"] = "worktree has uncommitted or ignored files"
            elif clean:
                item["worktree"] = sp["cwd"]
        out.append(item)
    return out


def run(days: int = RETENTION_DAYS, apply: bool = False) -> str:
    items = plan(days)
    old_telemetry = telemetry.prune(dry_run=not apply)
    removed = kept = freed = 0
    lines = []
    for item in items:
        if item.get("skip"):
            kept += 1
            lines.append(f"keep {item['job']}: {item['skip']}")
            continue
        if apply:
            if item["worktree"]:
                sp = jobs.spec(item["job"])
                done = subprocess.run(["git", "-C", sp["repo"], "worktree", "remove", item["worktree"]],
                                      capture_output=True, text=True, check=False)  # fmt: skip
                if done.returncode != 0:
                    kept += 1
                    lines.append(f"keep {item['job']}: worktree not removed ({done.stderr.strip()[:80]})")
                    continue
            shutil.rmtree(item["folder"])
        removed += 1
        freed += item["bytes"]
    verb = "removed" if apply else "would remove"
    head = f"{verb} {removed} job folders ({freed / 1e6:.1f} MB), kept {kept}; {verb} {len(old_telemetry)} telemetry files"
    usage = ", ".join(f"{k} {v / 1e6:.1f} MB" for k, v in du().items())
    return "\n".join([head, f"disk: {usage}", *lines[:20]] + ([] if apply else ["(dry run: add --apply to delete)"]))
