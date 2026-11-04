"""A CODEX_HOME for jobs: same state as ~/.codex, but a config without `mcp_servers`.

Every codex job otherwise starts all MCP servers of the interactive config (ssh tunnels,
browser bridge: about 10 processes and 150-300 MB per job). Jobs never use them.
Everything except config.toml is a symlink to the source home, so auth refresh, sessions
(resume, rate-limit reader) and skills stay shared. Interactive chats keep ~/.codex.
Disable with SUBTASK_CODEX_KEEP_MCP=1.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HEADER = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*(#.*)?$")
SKIP = ("config.toml", "log", "logs_2.sqlite", "tmp", "backups", "worktrees")


def strip_mcp_servers(text: str) -> str:
    """Drop every `[mcp_servers...]` table (and inline `mcp_servers = ...` line) from a TOML text."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        m = HEADER.match(line)
        if m:
            name = m.group(1).replace('"', "").replace(" ", "")
            skipping = name == "mcp_servers" or name.startswith("mcp_servers.")
        elif not skipping and re.match(r"^\s*mcp_servers\s*=", line):
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def _skipped(name: str) -> bool:
    return (
        name in SKIP
        or name.startswith("config.toml")
        or any(t in name for t in (".bak", "backup", "before-"))
    )


def prepare(source: Path | None = None, target: Path | None = None) -> Path:
    """Build (or refresh) the job home and return it. Falls back to the source home on any error."""
    source = source or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    target = target or Path.home() / ".codex-jobs"
    try:
        target.mkdir(mode=0o700, exist_ok=True)
        for entry in source.iterdir():
            if _skipped(entry.name):
                continue
            link = target / entry.name
            if link.is_symlink() and os.readlink(link) == str(entry):
                continue
            if link.exists() or link.is_symlink():
                continue  # a real file/dir the job home owns: leave it
            link.symlink_to(entry)
        for name in ("log", "tmp"):
            (target / name).mkdir(exist_ok=True)
        cfg = source / "config.toml"
        text = strip_mcp_servers(cfg.read_text()) if cfg.is_file() else ""
        dest = target / "config.toml"
        if not dest.is_file() or dest.read_text() != text:
            tmp = target / f".config.toml.{os.getpid()}"
            tmp.write_text(text)
            tmp.replace(dest)
        return target
    except OSError:
        return source
