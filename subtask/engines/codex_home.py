"""A CODEX_HOME for jobs: same state as ~/.codex, but a config without `mcp_servers`.

Every codex job otherwise starts all MCP servers of the interactive config (ssh tunnels,
browser bridge: about 10 processes and 150-300 MB per job). Jobs never use them.
Everything except config.toml is a symlink to the source home, so auth refresh, sessions
(resume, rate-limit reader) and skills stay shared. Interactive chats keep ~/.codex.
Disable with SUBTASK_CODEX_KEEP_MCP=1.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

SKIP = ("config.toml", "log", "logs_2.sqlite", "tmp", "backups", "worktrees")


def _skip_quoted(text: str, i: int) -> int:
    """Index just after the single-line quoted string that starts at text[i]."""
    quote = text[i]
    j = i + 1
    while j < len(text) and text[j] != quote:
        j += 2 if quote == '"' and text[j] == "\\" else 1
    return j + 1


def _parse_key(text: str, i: int, stop: str) -> list[str]:
    """Read a dotted, possibly quoted TOML key from text[i:] up to `stop`; return its parts."""
    parts: list[str] = []
    cur = ""
    while i < len(text) and text[i] != stop:
        c = text[i]
        if c in "\"'":
            end = _skip_quoted(text, i)
            cur += text[i + 1 : end - 1]
            i = end
            continue
        if c == ".":
            parts.append(cur)
            cur = ""
        elif not c.isspace():
            cur += c
        i += 1
    if i >= len(text):
        raise ValueError("key not terminated")
    return [*parts, cur]


def _advance(line: str, ml: str, depth: int) -> tuple[str, int]:
    """Scan one line of a value: track multi-line strings and open brackets; stop at a comment."""
    i = 0
    while i < len(line):
        if ml:
            j = line.find(ml, i)
            if j < 0:
                return ml, depth
            i, ml = j + 3, ""
            continue
        c = line[i]
        if c in "\"'":
            if line.startswith(c * 3, i):
                ml, i = c * 3, i + 3
            else:
                i = _skip_quoted(line, i)
            continue
        if c == "#":
            break
        depth += c in "[{"
        depth -= c in "]}"
        i += 1
    return ml, depth


def strip_mcp_servers(text: str) -> str:
    """Drop the root `mcp_servers` table from a TOML text, following the TOML structure.

    Removes `[mcp_servers]`, `[mcp_servers.x.y]`, `[[mcp_servers.x]]`, `mcp_servers = {...}`
    and dotted `mcp_servers.x = ...` keys (also multi-line values). Keeps lines that only look
    like headers inside multi-line strings or arrays, and `mcp_servers` keys of other tables.
    Raises ValueError if the result is not valid TOML or still has the key, so the caller
    never writes a half-stripped config.
    """
    out: list[str] = []
    in_mcp_table = False  # inside a [mcp_servers...] table
    in_root = True  # before the first header: keys belong to the root table
    drop = False  # the current key/value statement is dropped
    ml, depth = "", 0
    for line in text.splitlines(keepends=True):
        if ml or depth:  # continuation of a multi-line value
            ml, depth = _advance(line, ml, depth)
            if not (drop or in_mcp_table):
                out.append(line)
            continue
        body = line.strip()
        if not body or body.startswith("#"):
            if not in_mcp_table:
                out.append(line)
            continue
        if body.startswith("["):
            parts = _parse_key(body, 2 if body.startswith("[[") else 1, "]")
            in_root = False
            in_mcp_table = parts[0] == "mcp_servers"
            drop = False
            if not in_mcp_table:
                out.append(line)
            continue
        eq = body.index("=") if "=" in body else -1
        if eq < 0:
            raise ValueError("line is neither header nor key/value")
        drop = in_root and _parse_key(body[: eq + 1], 0, "=")[0] == "mcp_servers"
        ml, depth = _advance(body[eq + 1 :], "", 0)
        if not (drop or in_mcp_table):
            out.append(line)
    if ml or depth:
        raise ValueError("unterminated value in config")
    result = "".join(out)
    if "mcp_servers" in tomllib.loads(result):
        raise ValueError("mcp_servers still present")
    return result


def _skipped(entry: Path) -> bool:
    name = entry.name
    if name in SKIP:
        return True
    return not entry.is_dir() and (
        name.startswith("config.toml") or any(t in name for t in (".bak", "backup", "before-"))
    )


def prepare(source: Path | None = None, target: Path | None = None) -> Path:
    """Build (or refresh) the job home and return it. Falls back to the source home on any error."""
    source = source or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    target = target or Path.home() / ".codex-jobs"
    try:
        target.mkdir(mode=0o700, exist_ok=True)
        for entry in source.iterdir():
            if _skipped(entry):
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
    except (OSError, ValueError):  # ValueError: config not strippable safely, keep the source home
        return source
