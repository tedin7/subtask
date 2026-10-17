"""Merge Python files by top-level symbol instead of by line.

Each job's change to a file is read as a set of replaced, added or removed
top-level functions and classes. When the jobs' sets do not overlap, and none
of them changes module-level statements (imports, constants), the symbols are
grafted onto the base text one by one. Anything else is a conflict: nothing is
guessed.
"""

from __future__ import annotations

import ast

MODULE = "<module>"
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class Conflict(Exception):
    pass


def _parts(text: str) -> tuple[dict[str, tuple[int, int, str]], list[str]]:
    """{name: (first line, last line, source)} for top-level defs, and the other statements' source."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    defs, other = {}, []
    for node in tree.body:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        end = node.end_lineno or node.lineno
        source = "".join(lines[start - 1 : end])
        if isinstance(node, DEFS):
            defs[node.name] = (start, end, source)
        else:
            other.append(source)
    return defs, other


def changed_symbols(old: str, new: str) -> set[str]:
    """Top-level names whose source differs; MODULE when other statements differ. Raises SyntaxError."""
    old_defs, old_other = _parts(old) if old else ({}, [])
    new_defs, new_other = _parts(new) if new else ({}, [])
    names = {
        n
        for n in old_defs.keys() | new_defs.keys()
        if (old_defs.get(n) or (0, 0, ""))[2] != (new_defs.get(n) or (0, 0, ""))[2]
    }
    if old_other != new_other:
        names.add(MODULE)
    return names


def graft(base: str, versions: list[tuple[str, str, str]]) -> str:
    """Apply each (job id, its base text, its new text) symbol by symbol onto base. Raises Conflict."""
    try:
        base_defs, _ = _parts(base)
    except SyntaxError as exc:
        raise Conflict(f"base is not valid Python ({exc.msg})") from exc
    owner: dict[str, str] = {}
    edits: list[tuple[str, str | None]] = []  # (name, new source or None for removed)
    for job_id, old, text in versions:
        try:
            new_defs, _ = _parts(text)
            old_defs, _ = _parts(old) if old else ({}, [])
            names = changed_symbols(old, text)
        except SyntaxError as exc:
            raise Conflict(f"{job_id}: not valid Python ({exc.msg})") from exc
        if MODULE in names:
            raise Conflict(f"{job_id} changes module-level statements (imports or constants)")
        for name in names:
            if name in owner:
                raise Conflict(f"{owner[name]} and {job_id} both change {name}")
            owner[name] = job_id
            if (base_defs.get(name) or (0, 0, ""))[2] != (old_defs.get(name) or (0, 0, ""))[2]:
                raise Conflict(f"{name} changed on the target too since {job_id} started")
            edits.append((name, new_defs[name][2] if name in new_defs else None))
    lines = base.splitlines(keepends=True)
    replaced = sorted((e for e in edits if e[0] in base_defs), key=lambda e: -base_defs[e[0]][0])
    for name, source in replaced:
        start, end, _ = base_defs[name]
        lines[start - 1 : end] = [source] if source is not None else []
    text = "".join(lines)
    for name, source in sorted(e for e in edits if e[0] not in base_defs):
        if source is not None:
            text = text.rstrip("\n") + "\n\n\n" + source.rstrip("\n") + "\n"
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise Conflict(f"merged file is not valid Python ({exc.msg})") from exc
    return text
