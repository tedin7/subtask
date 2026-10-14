"""Minimal text-level edits of a Codex config.toml: touch one key, keep every other byte."""

from __future__ import annotations

import re

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _table_span(lines: list[str], table: str) -> tuple[int, int] | None:
    """Index of the `[table]` header line and the index where its body ends."""
    start = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        name = m.group(1).replace('"', "").replace(" ", "")
        if start is not None:
            return start, i
        if name == table:
            start = i
    return (start, len(lines)) if start is not None else None


def set_key(text: str, table: str, key: str, value: str) -> str:
    """Set `key = value` (value already TOML-encoded) inside `[table]`; other lines untouched."""
    lines = text.splitlines(keepends=True)
    span = _table_span(lines, table)
    if span is None:
        raise ValueError(f"table [{table}] not found")
    start, end = span
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            eol = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f"{key} = {value}{eol}"
            return "".join(lines)
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, f"{key} = {value}\n")
    return "".join(lines)
