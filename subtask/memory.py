"""Repository engrams: short lessons learned when a check failed and a later attempt fixed it.

One JSONL file per repository under HOME/engrams (never inside the repo, so it
stays clean). Hard cap of MAX_ENGRAMS lines, least recently used dropped first;
the same lesson is stored once and its use count grows. New jobs touching the
same paths get the top three in their task.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import config

MAX_ENGRAMS = 50
TOP = 3
ERROR = re.compile(r"error|failed|exception|traceback|assert", re.IGNORECASE)


def _file(repo: str) -> Path:
    key = hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:16]
    return config.HOME / "engrams" / f"{key}.jsonl"


def _load(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:  # a torn line costs that line, never the whole file
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [r for r in rows if isinstance(r, dict) and r.get("lesson")]


def _save(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r.get("last_used", 0), reverse=True)[:MAX_ENGRAMS]
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)


def _edit(repo: str, change) -> list[dict]:
    path = _file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = change(_load(path))
        _save(path, rows)
    return rows


def _first_error(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    hit = next((line for line in lines if ERROR.search(line)), lines[-1] if lines else "")
    return hit[:120]


def _scope(files: list[str]) -> str:
    dirs = [os.path.dirname(f) for f in files if f]
    if not dirs:
        return "*"
    common = os.path.commonpath(dirs) if all(dirs) else ""
    return common + "/" if common else "*"


def _changed(spec: dict, a: dict, b: dict) -> list[str]:
    heads = [(x["verification"].get("snapshot") or {}).get("head") for x in (a, b)]
    if not (spec.get("branch") and all(heads)):
        return list(spec.get("files") or [])
    done = subprocess.run(
        ["git", "-C", spec["cwd"], "diff", "--name-only", f"{heads[0]}..{heads[1]}"],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    return done.stdout.split() if done.returncode == 0 else []


def learn(spec: dict, attempts: list[dict]) -> dict | None:
    """Store an engram when the first verified attempt failed and the last one passed."""
    tried = [a for a in attempts if a.get("verification")]
    if len(tried) < 2 or tried[0]["verification"].get("exit_code") == 0:
        return None
    if tried[-1]["verification"].get("exit_code") != 0:
        return None
    repo = spec.get("repo") or spec.get("cwd")
    if not repo:
        return None
    files = _changed(spec, tried[0], tried[-1])
    command = str(tried[0]["verification"].get("command"))[:80]
    error = _first_error(str(tried[0]["verification"].get("output_tail") or ""))
    shown = ", ".join(files[:4]) or "the code"
    lesson = f"`{command}` failed with `{error}`; the fix changed {shown}"[:240]
    scope = _scope(files)
    key = hashlib.sha256(f"{scope}|{command}|{error}".encode()).hexdigest()[:12]
    now = time.time()
    entry = {"key": key, "scope": scope, "lesson": lesson, "created": now, "last_used": now, "uses": 1}

    def change(rows: list[dict]) -> list[dict]:
        for row in rows:
            if row.get("key") == key:
                row.update(lesson=lesson, last_used=now, uses=int(row.get("uses", 1)) + 1)
                return rows
        return [*rows, entry]

    _edit(repo, change)
    return entry


def relevant(repo: str, files: list[str] | None) -> list[dict]:
    files = [str(f) for f in files or []]

    def fits(row: dict) -> bool:
        scope = row.get("scope", "*")
        return scope == "*" or not files or any(f.startswith(scope) for f in files)

    rows = [r for r in _load(_file(repo)) if fits(r)]
    return sorted(rows, key=lambda r: (r.get("uses", 1), r.get("last_used", 0)), reverse=True)[:TOP]


def inject(repo: str, files: list[str] | None) -> str:
    """The lessons block for a new job; marks the chosen engrams as used (LRU)."""
    chosen = relevant(repo, files)
    if not chosen:
        return ""
    keys, now = {r["key"] for r in chosen}, time.time()

    def touch(rows: list[dict]) -> list[dict]:
        for row in rows:
            if row.get("key") in keys:
                row["last_used"] = now
        return rows

    _edit(repo, touch)
    body = "\n".join(f"- [{r['scope']}] {r['lesson']}" for r in chosen)
    return f"<repo_lessons>\nLearned on this repository by earlier jobs:\n{body}\n</repo_lessons>"
