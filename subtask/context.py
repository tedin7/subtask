"""Context resolved on the server before a job starts, so the worker does not spend turns finding it.

References:
  file:PATH            file:PATH:START-END     (inside the job's folder only)
  git:diff             git:diff:REF
  subtask:ID:summary   subtask:ID:diff
Plus an optional repo map: tracked files with their top-level symbols, cached per HEAD.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path

from . import config

MAX_REF = 20_000
MAX_TOTAL = 60_000
MAX_MAP = 15_000
SYMBOL = {
    ".ts": re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const)\s+(\w+)", re.MULTILINE
    ),
    ".js": re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+(\w+)", re.MULTILINE),
    ".go": re.compile(r"^(?:func|type)\s+(?:\([^)]*\)\s*)?(\w+)", re.MULTILINE),
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+(\w+)", re.MULTILINE),
}
SYMBOL[".tsx"] = SYMBOL[".ts"]


def _git(root: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30, check=False)
    return done.stdout if done.returncode == 0 else ""


def _file(root: Path, ref: str) -> str:
    match = re.fullmatch(r"(.+?)(?::(\d+)-(\d+))?", ref)
    assert match
    path = (root / match.group(1)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"outside the job folder: {match.group(1)}")
    if not path.is_file():
        raise ValueError(f"no such file: {match.group(1)}")
    lines = path.read_text(errors="replace").splitlines()
    if match.group(2):
        a, b = int(match.group(2)), int(match.group(3))
        lines = lines[max(a - 1, 0) : b]
    return "\n".join(lines)


def _subtask(ref: str) -> str:
    from . import core, jobs

    job_id, _, what = ref.partition(":")
    if not core.known(job_id):
        raise ValueError(f"unknown job: {job_id}")
    if what == "summary":
        path = jobs.job_dir(job_id) / "result.md"
        return core.summary_of(path.read_text()) if path.is_file() else ""
    if what == "diff":
        sp = jobs.spec(job_id)
        if not sp.get("branch"):
            raise ValueError(f"{job_id} has no branch")
        return _git(sp["repo"], "diff", f"{sp['base']}..{sp['branch']}")
    raise ValueError("subtask refs end in :summary or :diff")


def resolve(refs: list[str], root: str) -> str:
    """The <subtask_context> block for `refs`. Raises ValueError on a bad reference."""
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise ValueError("context_refs must be a list of strings")
    parts, total = [], 0
    for ref in refs:
        kind, _, rest = ref.partition(":")
        if kind == "file":
            body = _file(Path(root), rest)
        elif kind == "git":
            if rest != "diff" and not rest.startswith("diff:"):
                raise ValueError(f"unknown git ref: {ref}")
            target = rest.removeprefix("diff").removeprefix(":")
            if target.startswith("-"):
                raise ValueError(f"invalid git ref: {ref}")
            body = _git(root, "diff", *([target] if target else []))
        elif kind == "subtask":
            body = _subtask(rest)
        else:
            raise ValueError(f"unknown reference: {ref}")
        body = body[:MAX_REF] + ("\n… cut" if len(body) > MAX_REF else "")
        if total + len(body) > MAX_TOTAL:
            parts.append(f'<ref name="{ref}">left out: context limit reached</ref>')
            continue
        total += len(body)
        parts.append(f'<ref name="{ref}">\n{body}\n</ref>')
    return "<subtask_context>\n" + "\n".join(parts) + "\n</subtask_context>" if parts else ""


def _symbols(path: Path) -> list[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return []
        return [
            n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
    pattern = SYMBOL.get(path.suffix)
    return pattern.findall(text) if pattern else []


def repomap(root: str) -> str:
    """Tracked files with their top-level symbols; cached until HEAD changes."""
    head = _git(root, "rev-parse", "HEAD").strip()
    if not head:
        return ""
    key = hashlib.sha256(f"{Path(root).resolve()}@{head}".encode()).hexdigest()[:16]
    cache = config.HOME / "repomap" / f"{key}.txt"
    if cache.is_file():
        return cache.read_text()
    lines = []
    for name in _git(root, "ls-files").splitlines():
        symbols = _symbols(Path(root) / name)
        lines.append(f"{name}: {', '.join(symbols[:12])}" if symbols else name)
    text = "\n".join(lines)
    text = text[:MAX_MAP] + ("\n… map cut" if len(text) > MAX_MAP else "")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text
