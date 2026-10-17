"""map_reduce: one call turns a glob into batched jobs, run as a group with a concurrency limit."""

from __future__ import annotations

import os
from pathlib import Path

MAX_FILES = 2000


def files_for(root: str, pattern: str) -> list[str]:
    base = Path(root).resolve()
    found = []
    for path in sorted(base.glob(pattern)):
        rel = path.relative_to(base)
        if path.is_file() and ".git" not in rel.parts:
            found.append(str(rel))
        if len(found) >= MAX_FILES:
            break
    return found


def map_reduce(
    glob_pattern: str,
    prompt_template: str,
    batch_size: int = 5,
    concurrency: int = 3,
    verify_cmd: str | None = None,
    isolation: str = "worktree",
    repo: str | None = None,
    cwd: str | None = None,
    engine: str = "auto",
    kind: str = "code",
    max_usd: float | None = None,
) -> str:
    from . import core, groups

    if not isinstance(prompt_template, str) or "{{files}}" not in prompt_template:
        return "prompt_template must contain {{files}}"
    if (
        not isinstance(glob_pattern, str)
        or not glob_pattern
        or glob_pattern.startswith("/")
        or ".." in glob_pattern
    ):
        return "glob_pattern must be relative and stay inside the folder"
    for name, value, top in (("batch_size", batch_size, 50), ("concurrency", concurrency, 16)):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= top:
            return f"{name} must be between 1 and {top}"
    root = str(Path(repo or cwd or os.getcwd()).resolve())
    if isolation == "worktree" and not core._repo_of(root):
        return "isolation worktree needs a git repository"
    files = files_for(root, glob_pattern)
    if not files:
        return f"no files match {glob_pattern} in {root}"
    batches = [files[i : i + batch_size] for i in range(0, len(files), batch_size)]
    where = {"repo": repo} if repo else {"cwd": root}
    tasks = [
        {
            "task": prompt_template.replace("{{files}}", ", ".join(batch)),
            "engine": engine,
            "kind": kind,
            "isolation": isolation,
            "verify": verify_cmd,
            "max_usd": max_usd,
            "files": batch,
            **where,
        }
        for batch in batches
    ]
    head = f"map_reduce: {len(files)} files in {len(batches)} batches, {concurrency} at a time\n"
    return head + groups.create(tasks, concurrency=concurrency, kind="map_reduce", label=glob_pattern)
