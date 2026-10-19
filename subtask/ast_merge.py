"""Merge Python files by top-level symbol instead of by line.

Each job's change to a file is read as a set of replaced, added or removed
top-level functions and classes. When the jobs' sets do not overlap, and none
of them changes module-level statements (imports, constants), the symbols are
grafted onto the base text one by one. Anything else is a conflict: nothing is
guessed.
"""

from __future__ import annotations

import ast
import re

MODULE = "<module>"
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class Conflict(Exception):
    pass


def _lines(text: str) -> list[str]:
    """Lines as ast counts them: only \\n ends a line (splitlines also splits on form feeds)."""
    if re.search(r"\r(?!\n)", text):
        raise SyntaxError("lone carriage return")
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def _main_guard(tree: ast.Module) -> int | None:
    """First line of a top-level `if __name__ == "__main__":`, where new symbols must go before."""
    for node in tree.body:
        test = getattr(node, "test", None)
        is_compare = isinstance(node, ast.If) and isinstance(test, ast.Compare)
        if is_compare and isinstance(test.left, ast.Name) and test.left.id == "__name__":
            return node.lineno
    return None


def _parts(text: str) -> tuple[dict[str, tuple[int, int, str]], list[str]]:
    """{name: (first line, last line, source)} for top-level defs, and everything else.

    A second def with the same name is keyed name#2. Everything outside the defs (statements,
    comments) is kept as non-blank text, so a change there is a module-level change.
    """
    tree = ast.parse(text)
    lines = _lines(text)
    defs: dict[str, tuple[int, int, str]] = {}
    taken = [False] * (len(lines) + 1)
    for node in tree.body:
        if not isinstance(node, DEFS):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno or node.lineno
        name, n = node.name, 1
        while name in defs:
            n += 1
            name = f"{node.name}#{n}"
        defs[name] = (start, end, "".join(lines[start - 1 : end]))
        for i in range(start, end + 1):
            taken[i] = True
    other = [line.strip() for i, line in enumerate(lines, 1) if not taken[i] and line.strip()]
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
    lines = _lines(base)
    added = "".join(
        source.rstrip("\n") + "\n\n\n"
        for _, source in sorted(e for e in edits if e[0] not in base_defs)
        if source
    )
    guard = _main_guard(ast.parse(base))
    # (first line, last line, new text); applied bottom-up so earlier line numbers stay valid
    ops = [(base_defs[n][0], base_defs[n][1], src or "") for n, src in edits if n in base_defs]
    if added and guard is not None:  # before the main block, which may call them
        ops.append((guard, guard - 1, added))
    for start, end, src in sorted(ops, key=lambda o: (-o[0], -o[1])):
        lines[start - 1 : end] = [src] if src else []
    text = "".join(lines)
    if added and guard is None:
        text = text.rstrip("\n") + "\n\n\n" + added.rstrip("\n") + "\n"
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise Conflict(f"merged file is not valid Python ({exc.msg})") from exc
    return text
