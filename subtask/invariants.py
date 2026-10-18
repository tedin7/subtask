"""Static checks that run before `verify` counts: a job may not pass by weakening the checks.

Compared with the job's base commit, over the whole worktree (untracked files included):
  no_test_deletion  test_* functions in test files may not decrease; no new skip markers
  no_suppression    no new `# type: ignore`, `# noqa` or `# pragma: no cover`
  allowed_paths     files changed outside these paths are reverted and committed back
  symbol_scope      in a scoped Python file only the named top-level symbols may change
No model, no network: git and ast only.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
import tempfile

from . import ast_merge

NAMES = ("no_test_deletion", "no_suppression")
DEFAULT = list(NAMES)
SUPPRESS = re.compile(
    r"#\s*(?:type:\s*ignore|noqa|pragma:\s*no\s*cover|pyright:\s*ignore|pylint:\s*disable|mypy:\s*ignore)"
)
SKIP = re.compile(
    r"(?:pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|importorskip|xfail)\(|unittest\.skip)"
)


def _git(cwd: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, **env} if env else None,
    )  # fmt: skip


def validate(invariants: object, allowed_paths: object, symbol_scope: object) -> str | None:
    if invariants is not None and (
        not isinstance(invariants, list) or any(i not in NAMES for i in invariants)
    ):
        return "invariants must be a list of: " + ", ".join(NAMES)
    for name, value in (("allowed_paths", allowed_paths), ("symbol_scope", symbol_scope)):
        if value is not None and (
            not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value)
        ):
            return f"{name} must be a non-empty list of strings"
    for item in symbol_scope or []:
        path, sep, name = item.partition("::")
        if not sep or not path.endswith(".py") or not name.isidentifier():
            return f"symbol_scope entries look like path.py::name, got {item!r}"
    return None


def _changed(cwd: str, base: str) -> list[tuple[str, str]]:
    """(status, path) of every file that differs from base, untracked included."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git(cwd, "add", "-A", ".", env=env)
        out = _git(cwd, "diff", "--cached", "--name-status", "--no-renames", "-z", base, env=env).stdout
    parts = out.split("\0")
    return [(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2) if parts[i]]


def _old(cwd: str, base: str, path: str) -> str:
    done = _git(cwd, "show", f"{base}:{path}")
    return done.stdout if done.returncode == 0 else ""


def _new(cwd: str, path: str) -> str:
    try:
        with open(os.path.join(cwd, path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _is_test(path: str) -> bool:
    name = os.path.basename(path)
    return path.endswith(".py") and (
        name == "conftest.py"
        or name.startswith("test_") or name.endswith("_test.py") or "tests/" in path or path.startswith("test/")
    )


def _tests(text: str) -> int:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return 0
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
    )


def _count(pattern: re.Pattern, text: str) -> int:
    return len(pattern.findall(text))


def _allowed(path: str, allowed: list[str]) -> bool:
    for rule in allowed:
        rule = os.path.normpath(rule).rstrip("/")
        if path == rule or path.startswith(rule + "/") or fnmatch.fnmatch(path, rule):
            return True
    return False


def revert_outside(cwd: str, base: str, allowed: list[str]) -> list[str]:
    """Put back every file changed outside `allowed`, and commit the revert. Returns the paths."""
    out = [(s, p) for s, p in _changed(cwd, base) if not _allowed(p, allowed)]
    for status, path in out:
        if status == "A":
            _git(cwd, "rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", path)
            try:
                os.remove(os.path.join(cwd, path))
            except OSError:
                pass
        else:
            _git(cwd, "checkout", base, "--", path)
    if out:
        _git(cwd, "add", "-A", "--", *[p for _, p in out])
        _git(cwd, "-c", "user.name=subtask", "-c", "user.email=subtask@localhost", "commit", "-q",
             "--no-verify", "-m", "subtask: revert changes outside allowed_paths")  # fmt: skip
    return [p for _, p in out]


def check(cwd: str, base: str, invariants: list[str], symbol_scope: list[str] | None = None) -> list[str]:
    """The violations, each one line a model can act on. Empty means clean."""
    found = []
    scoped: dict[str, set[str]] = {}
    for item in symbol_scope or []:
        path, _, name = item.partition("::")
        scoped.setdefault(path, set()).add(name)
    for status, path in _changed(cwd, base):
        old = _old(cwd, base, path) if status != "A" else ""
        new = _new(cwd, path) if status != "D" else ""
        if "no_test_deletion" in invariants and _is_test(path):
            before, after = _tests(old), _tests(new)
            if after < before:
                found.append(f"no_test_deletion: {path} has {after} test functions, base had {before}")
            if _count(SKIP, new) > _count(SKIP, old):
                found.append(f"no_test_deletion: {path} adds a skip/xfail marker")
        if "no_suppression" in invariants and _count(SUPPRESS, new) > _count(SUPPRESS, old):
            found.append(f"no_suppression: {path} adds a type: ignore / noqa / pragma: no cover comment")
        if path in scoped:
            try:
                touched = ast_merge.changed_symbols(old, new)
            except SyntaxError as exc:
                found.append(f"symbol_scope: {path} is not valid Python ({exc.msg})")
                continue
            extra = sorted(touched - scoped[path])
            if extra:
                found.append(
                    f"symbol_scope: {path} changes {', '.join(extra)} outside {sorted(scoped[path])}"
                )
    return found
