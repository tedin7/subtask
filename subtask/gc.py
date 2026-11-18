"""Retention and disk usage: report first, delete only with --apply, never user work.

Removable: folders of finished jobs older than the retention, whose worktree is
already gone or clean (no uncommitted files; ignored files only if regenerable,
like node_modules or Cargo's `target/`), telemetry older
than its retention. Never removed: active jobs, dirty worktrees. Branches: only with `--branches`,
and only those already contained in the remote default branch (see branch_plan).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, jobs, telemetry, worktree_guard

RETENTION_DAYS = int(os.environ.get("SUBTASK_JOB_RETENTION_DAYS", "30"))


DU_WORKERS = 16


def _walk_size(path: Path, linked: set[tuple[int, int]], lock: threading.Lock) -> int:
    """Bytes on disk below one directory: hard-linked files count once (inodes shared across threads)."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                except OSError:
                    continue
                if info.st_nlink > 1:
                    inode = (info.st_dev, info.st_ino)
                    with lock:
                        if inode in linked:
                            continue
                        linked.add(inode)
                total += info.st_blocks * 512
    return total


def _size(path: Path) -> int:
    """Bytes on disk: a file with many hard links counts once, a sparse file only for its blocks.

    T-1002622: with thousands of job folders a serial walk waits on one disk read at a time and
    ran past 120 s; the first-level folders are walked by a pool of threads (reads release the GIL).
    """
    linked: set[tuple[int, int]] = set()
    lock = threading.Lock()
    total = 0
    subdirs: list[Path] = []
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(Path(entry.path))
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if info.st_nlink > 1:
                inode = (info.st_dev, info.st_ino)
                if inode in linked:
                    continue
                linked.add(inode)
            total += info.st_blocks * 512
    except OSError:
        return 0
    with ThreadPoolExecutor(max_workers=DU_WORKERS) as pool:
        total += sum(pool.map(lambda d: _walk_size(d, linked, lock), subdirs))
    return total


def du_parts():
    """Yield (name, bytes) one folder at a time, so a slow disk shows progress instead of silence (T-1002622)."""
    parts = {
        "jobs": config.JOBS,
        "worktrees": config.WORKTREES,
        "ledger": config.HOME / "ledger",
        "telemetry": config.HOME / "telemetry",
    }
    for name, path in parts.items():
        yield name, _size(path) if path.exists() else 0


def du() -> dict:
    return dict(du_parts())


# T-7258b: ".reports" holds job reports. Nothing in the copy records that the coordinator
# collected them (raccogli_rapporto only copies out), so a non-empty .reports always keeps the copy.
REPORTS = ".reports"

# Ignored entries that tools regenerate: they do not hold user work.
REGENERABLE = frozenset(
    {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
     ".next", ".turbo", ".cache"}
)  # fmt: skip
# Cargo writes this signature at the top of every `target/`: it marks a regenerable build cache.
# T-1000599: an arvx copy kept 3.7 GB of Rust binaries and could never be reclaimed without it.
CARGO_TAG = "Signature: 8a477f597d28d172789f06886806bc55"
GIT_TIMEOUT = 60


def _cargo_target(path: Path, parts: tuple[str, ...]) -> bool:
    """True for a `target/` directory signed by Cargo, which a build recreates."""
    if "target" not in parts:
        return False
    target = path.joinpath(*parts)
    try:
        if target.is_symlink() or not target.is_dir():
            return False
        tag = target / "CACHEDIR.TAG"
        return not tag.is_symlink() and tag.read_text(errors="replace").startswith(CARGO_TAG)
    except OSError:
        return False


def _regenerable(path: Path, line: str) -> bool:
    """An ignored entry whose name is a known cache or a signed Cargo target.

    T-1002124: a script naming a folder (check.sh and `artifacts/`) does not make it deletable: reports and
    proofs of uncertain ownership are kept. Only entries a build recreates are listed here.
    """
    parts = Path(line.rstrip("/")).parts
    return bool(parts) and (bool(set(parts) & REGENERABLE) or _cargo_target(path, parts))


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


def worktree_clean(path: str) -> bool | None:
    """None: gone. True: nothing to lose. False: dirty, unknown or user ignored files.

    Plain `git status --ignored` walks every node_modules (15+ minutes over 800 copies) and
    treats them as work. Here ignored folders are only listed, never entered, and the
    regenerable ones (node_modules, .venv, caches, Cargo target) do not count.
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
        if not _regenerable(Path(path), line):
            return False
    return True


def prune_regenerable(path: str) -> list[str]:
    """Delete the ignored build folders of a job copy that cleanup keeps (T-1000599).

    A finished copy is often kept for its branch, and the build output inside it (Cargo
    `target/`, node_modules, caches) can be gigabytes that a later build recreates anyway.
    Only entries accepted by `_regenerable` are removed: nothing tracked, untracked or
    otherwise ignored is touched. Returns the removed names.
    """
    root = Path(os.path.realpath(path))
    ignored = _git(str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    if ignored is None or ignored.returncode != 0:
        return []
    removed = []
    for line in ignored.stdout.splitlines():
        if not _regenerable(root, line):
            continue
        target = root.joinpath(*Path(line.rstrip("/")).parts)
        real = Path(os.path.realpath(target))
        if target.is_symlink() or not target.is_dir() or real == root or not real.is_relative_to(root):
            continue
        try:
            shutil.rmtree(target)
        except OSError:
            continue
        removed.append(line.rstrip("/"))
    return removed


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
            clean = worktree_clean(sp["cwd"])
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
    if item["worktree"] and worktree_clean(item["worktree"]) is False:
        return "worktree has uncommitted or ignored files"
    return None


BRANCH_PREFIX = "refs/heads/subtask/st-"
BRANCH_LOG = "gc-branches.log"


def _default_base(repo: str) -> str | None:
    """The remote default branch (origin/HEAD), or None: never guessed between main and master."""
    done = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if done is None or done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout.strip()


def _repos() -> list[str]:
    seen: dict[str, None] = {}
    for job_id in jobs.all_jobs():
        repo = jobs.spec(job_id).get("repo")
        if repo and Path(repo).is_dir():
            seen[str(repo)] = None
    return list(seen)


def _checked_out(repo: str) -> set[str] | None:
    done = _git(repo, "worktree", "list", "--porcelain")
    if done is None or done.returncode != 0:
        return None
    return {line.split(" ", 1)[1] for line in done.stdout.splitlines() if line.startswith("branch ")}


def branch_plan() -> list[dict]:
    """Local `subtask/st-*` branches whose tip is an ancestor of the remote default branch.

    Such a branch holds no commit that the default branch lacks, so deleting it loses no work.
    Kept: a branch with a worktree, one whose job is not in a final state, and any repo whose
    default branch or worktree list cannot be read. Equivalent patches (squash) are not touched.
    """
    out: list[dict] = []
    for repo in _repos():
        base = _default_base(repo)
        in_use = _checked_out(repo)
        if base is None or in_use is None:
            out.append({"repo": repo, "skip": "origin/HEAD or worktree list not readable"})
            continue
        listing = _git(
            repo, "for-each-ref", "--merged", base, "--format=%(refname) %(objectname)", BRANCH_PREFIX[:-3]
        )
        if listing is None or listing.returncode != 0:
            out.append({"repo": repo, "skip": "branch list not readable"})
            continue
        for line in listing.stdout.splitlines():
            ref, _, sha = line.partition(" ")
            if not ref.startswith(BRANCH_PREFIX) or ref in in_use:
                continue
            job = ref.removeprefix("refs/heads/subtask/")
            state = jobs.read_json(jobs.job_dir(job) / "status.json", {}).get("state")
            if state is not None and state not in jobs.FINAL:
                continue
            out.append({"repo": repo, "base": base, "ref": ref, "sha": sha})
    return out


def _remove_branch(item: dict) -> str | None:
    """Delete one branch only if it still points at the planned sha, which is still integrated."""
    repo, ref, sha = item["repo"], item["ref"], item["sha"]
    now = _git(repo, "rev-parse", "--verify", "-q", ref + "^{commit}")
    if now is None or now.stdout.strip() != sha:
        return "branch moved after the plan"
    merged = _git(repo, "merge-base", "--is-ancestor", sha, item["base"])
    if merged is None or merged.returncode != 0:
        return "branch not integrated anymore"
    try:
        with (config.HOME / BRANCH_LOG).open("a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\t{ref.removeprefix('refs/heads/')}\t{sha}\t{repo}\n")  # fmt: skip
    except OSError as exc:
        return f"log not written: {exc}"  # no recovery line, no deletion
    done = subprocess.run(
        ["git", "-C", repo, "branch", "-D", ref.removeprefix("refs/heads/")],
        capture_output=True, text=True, check=False, env={**os.environ, "LC_ALL": "C"}, timeout=GIT_TIMEOUT,
    )  # fmt: skip
    if done.returncode != 0:
        return f"git refused: {done.stderr.strip()[:80]}"
    deleted = re.search(r"\(was ([0-9a-f]{7,40})\)", done.stdout)
    if deleted is None or not sha.startswith(deleted.group(1)):
        if deleted is not None:  # a different tip was deleted: put it back
            full = _git(repo, "rev-parse", "--verify", "-q", deleted.group(1) + "^{commit}")
            if full is not None and full.stdout.strip():
                _git(repo, "update-ref", ref, full.stdout.strip(), "")
        return "branch changed during deletion, restored"
    return None


def run_branches(apply: bool = False) -> str:
    """Delete integrated, idle `subtask/st-*` branches in every repo the jobs used (F-11589)."""
    removed = 0
    lines: list[str] = []
    for item in branch_plan():
        if item.get("skip"):
            lines.append(f"skip {item['repo']}: {item['skip']}")
            continue
        name = item["ref"].removeprefix("refs/heads/")
        if not apply:
            removed += 1
            continue
        why = _remove_branch(item)
        if why:
            lines.append(f"keep {name}: {why}")
        else:
            removed += 1
    verb = "removed" if apply else "would remove"
    return "\n".join(
        [f"{verb} {removed} branches", *lines[:20]] + ([] if apply else ["(dry run: add --apply to delete)"])
    )


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
    scope = (
        f"scope: all repositories; finished jobs older than {days} days only. "
        "Job counts are not the total number of jobs."
    )
    return "\n".join(
        [head, scope, "disk usage: run subtask du (may take time)", *lines[:20]]
        + ([] if apply else ["(dry run: add --apply to delete)"])
    )
