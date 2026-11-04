"""Retention and disk usage: report first, delete only with --apply, never user work.

Removable: folders of finished jobs older than the retention, whose worktree is
already gone or clean (no uncommitted files; ignored files only if regenerable,
like node_modules), telemetry older
than its retention. Never removed: active jobs, dirty worktrees, branches.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

from . import config, jobs, telemetry, worktree_guard

RETENTION_DAYS = int(os.environ.get("SUBTASK_JOB_RETENTION_DAYS", "30"))


def _size(path: Path) -> int:
    """Bytes on disk: a file with many hard links counts once, a sparse file only for its blocks."""
    total = 0
    linked: set[tuple[int, int]] = set()
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                info = (Path(root) / name).lstat()
            except OSError:
                continue
            if info.st_nlink > 1:
                inode = (info.st_dev, info.st_ino)
                if inode in linked:
                    continue
                linked.add(inode)
            total += info.st_blocks * 512
    return total


def du() -> dict:
    parts = {
        "jobs": config.JOBS,
        "worktrees": config.WORKTREES,
        "ledger": config.HOME / "ledger",
        "telemetry": config.HOME / "telemetry",
    }
    return {name: _size(path) if path.exists() else 0 for name, path in parts.items()}


# Ignored entries that tools regenerate: they do not hold user work.
REGENERABLE = frozenset(
    {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
     ".next", ".turbo", ".cache"}
)  # fmt: skip
GIT_TIMEOUT = 60


def _git(path: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", path, *args],
            capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return None


RECEIPT = ".collected"


def _reports_collected(reports: Path) -> bool:
    """True if `.reports` is absent or empty, or holds a receipt newer than every report.

    A collector that copied the reports elsewhere writes a regular file `.reports/.collected`
    (any content, e.g. time and destination) after the copy. Only its modification time is read.
    A report changed after the receipt, a symlink or any unreadable entry means: keep.
    """
    try:
        if reports.is_symlink():
            return False
        if not reports.exists():
            return True
        if not reports.is_dir():
            return False
        newest = 0
        receipt = None
        for entry in reports.iterdir():
            info = entry.lstat()
            if entry.name == RECEIPT:
                if not stat.S_ISREG(info.st_mode):
                    return False
                receipt = info.st_mtime_ns
            elif stat.S_ISREG(info.st_mode):
                newest = max(newest, info.st_mtime_ns)
            else:
                return False
        if newest == 0:
            return True  # nothing but, possibly, a receipt
        return receipt is not None and receipt >= newest
    except OSError:
        return False


def _worktree_clean(path: str) -> bool | None:
    """None: gone. True: nothing to lose. False: dirty, unknown or user ignored files.

    Plain `git status --ignored` walks every node_modules (15+ minutes over 800 copies) and
    treats them as work. Here ignored folders are only listed, never entered, and the
    regenerable ones (node_modules, .venv, caches) do not count.
    """
    if not Path(path).exists():
        return None
    # Reports are kept until the collector leaves a receipt (see _reports_collected).
    if not _reports_collected(Path(path) / ".reports"):
        return False
    status = _git(path, "status", "--porcelain")
    if status is None or status.returncode != 0 or status.stdout.strip():
        return False
    ignored = _git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    if ignored is None or ignored.returncode != 0:
        return False
    for line in ignored.stdout.splitlines():
        parts = Path(line.rstrip("/")).parts
        if parts[:1] == (".reports",):
            continue  # already judged by _reports_collected
        if not parts or not (set(parts) & REGENERABLE):
            return False
    return True


def plan(days: int = RETENTION_DAYS) -> list[dict]:
    cutoff = time.time() - days * 86400
    out = []
    for job_id in jobs.all_jobs():
        folder = jobs.job_dir(job_id)
        # The collector must not settle workers or touch the ledger while planning.
        # An active recorded state is kept, even when the worker may have died.
        st = jobs.read_json(folder / "status.json", {"state": "unknown"})
        if st.get("state") not in jobs.FINAL:
            continue
        sp = jobs.spec(job_id)
        if float(st.get("updated") or folder.stat().st_mtime) > cutoff:
            continue
        item = {"job": job_id, "folder": str(folder), "worktree": None}
        if sp.get("branch") and sp.get("cwd"):
            clean = _worktree_clean(sp["cwd"])
            if clean is False:
                item["skip"] = "worktree has uncommitted or ignored files"
            elif clean:
                blocker = worktree_guard.removal_blocker(sp)
                if blocker:
                    item["skip"] = blocker
                else:
                    item["worktree"] = sp["cwd"]
        out.append(item)
    return out


def _changed_since_plan(item: dict, days: int) -> str | None:
    """Re-read the job right before deleting: it may have been resumed after the plan (T-6435)."""
    st = jobs.read_json(Path(item["folder"]) / "status.json", {"state": "unknown"})
    if st.get("state") not in jobs.FINAL:
        return "job is active again"
    if float(st.get("updated") or 0) > time.time() - days * 86400:
        return "job was updated after the plan"
    if item["worktree"] and _worktree_clean(item["worktree"]) is False:
        return "worktree has uncommitted or ignored files"
    return None


def run(days: int = RETENTION_DAYS, apply: bool = False) -> str:
    items = plan(days)
    old_telemetry = telemetry.prune(dry_run=not apply)
    removed = kept = 0
    lines = []
    for item in items:
        if item.get("skip"):
            kept += 1
            lines.append(f"keep {item['job']}: {item['skip']}")
            continue
        if apply:
            changed = _changed_since_plan(item, days)
            if changed:
                kept += 1
                lines.append(f"keep {item['job']}: {changed}")
                continue
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
    verb = "removed" if apply else "would remove"
    head = f"{verb} {removed} job folders, kept {kept}; {verb} {len(old_telemetry)} telemetry files"
    return "\n".join(
        [head, "disk usage: run subtask du (may take time)", *lines[:20]]
        + ([] if apply else ["(dry run: add --apply to delete)"])
    )
