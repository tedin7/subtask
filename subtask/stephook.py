"""Automatic step announcements from a Claude Code PreToolUse hook, without the agent.

The agent does not have to call step(): before a file change or a commit, the hook
reads three facts and has the cheap narrator explain them to the user:

- the user's latest request (the "why"), from the session transcript;
- what the agent did so far (files read, commands run), from the transcript;
- the exact change about to happen (Edit/Write content, or the git command).

The explanation is returned as a hook `systemMessage`: shown to the user in the chat,
never sent to the agent, so the agent spends no turn and no token. At most once per
SUBTASK_STEP_EVERY_MIN minutes (default 20) per repository; a manual step() also counts.
Any failure lets the tool call through silently (logged in steps.jsonl).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

EVERY_S = float(os.environ.get("SUBTASK_STEP_EVERY_MIN", "20")) * 60
TIMEOUT_S = float(os.environ.get("SUBTASK_STEP_HOOK_TIMEOUT_S", "10"))
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_CHANGE = 2500
SYSTEM = (
    "You are an independent observer of a coding agent. You did not plan its work. "
    "From the facts below, explain to the operator, who is smart but not technical, what is about "
    "to happen and why: what the user asked, what the agent has done so far, what this change "
    "does. Short sentences, plain words, no em dashes, at most 5 bullets. Base every statement on "
    "the facts given; if the reason for the change is not clear from them, say so. If the change "
    "does not fit the user's request, say it first. Write in this language: {lang}."
)


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def read_transcript(path: str | None, max_bytes: int = 400_000) -> tuple[str, list[str]]:
    """(last user request, recent agent actions) from a Claude Code session transcript."""
    if not path or not Path(path).is_file():
        return "", []
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - max_bytes))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    request, actions = "", []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if row.get("type") == "user":
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            text = _text(content).strip()
            if text and not is_tool_result and not text.startswith("<"):
                request, actions = text, []  # a new request starts a new chain of actions
        elif row.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    actions.append(_action(b.get("name", ""), b.get("input") or {}))
    return request[-2000:], [a for a in actions if a][-12:]


def _action(name: str, args: dict) -> str:
    target = args.get("file_path") or args.get("path") or args.get("pattern") or args.get("command") or ""
    return f"{name} {str(target)[:120]}".strip()


def describe_change(tool: str, args: dict) -> str | None:
    if tool in EDIT_TOOLS:
        path = args.get("file_path") or args.get("notebook_path") or "?"
        if tool == "Write":
            body = f"new content:\n{str(args.get('content', ''))[:MAX_CHANGE]}"
        elif tool == "MultiEdit":
            parts = [
                f"- {e.get('old_string', '')[:300]}\n+ {e.get('new_string', '')[:300]}"
                for e in args.get("edits", [])
            ]
            body = "\n".join(parts)[:MAX_CHANGE]
        else:
            body = f"- {str(args.get('old_string', ''))[: MAX_CHANGE // 2]}\n+ {str(args.get('new_string', ''))[: MAX_CHANGE // 2]}"
        return f"{tool} {path}\n{body}"
    if tool == "Bash":
        cmd = str(args.get("command", ""))
        if "git commit" in cmd or "git merge" in cmd or "git push" in cmd:
            return f"Bash: {cmd[:MAX_CHANGE]}"
    return None


def _recent(repo: str) -> bool:
    from .core import STEPS

    if not STEPS.is_file():
        return False
    with STEPS.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 200_000))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    now = time.time()
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("repo") != repo:
            continue
        ts = row.get("epoch") or time.mktime(time.strptime(row["ts"], "%Y-%m-%d %H:%M:%S"))
        return now - float(ts) < EVERY_S
    return False


def _log(entry: dict) -> None:
    from . import config
    from .core import STEPS

    config.HOME.mkdir(parents=True, exist_ok=True)
    with STEPS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def handle(event: dict) -> dict | None:
    """The hook's JSON answer, or None to stay silent."""
    from . import narrate
    from .core import _repo_of
    from .redact import redact

    change = describe_change(str(event.get("tool_name", "")), event.get("tool_input") or {})
    if not change:
        return None
    args = event.get("tool_input") or {}
    path = args.get("file_path") or args.get("notebook_path") or event.get("cwd") or os.getcwd()
    repo = _repo_of(str(path)) or _repo_of(str(event.get("cwd") or "")) or str(event.get("cwd") or "")
    if not repo or _recent(repo):
        return None
    request, actions = read_transcript(event.get("transcript_path"))
    facts = (
        f"User's latest request:\n{request or '(not found)'}\n\n"
        f"Agent's recent actions:\n" + ("\n".join(actions) or "(none recorded)") + "\n\n"
        f"Change about to happen:\n{change}\n\nRepository state:\n{narrate.repo_context(repo)}"
    )
    lang = os.environ.get("SUBTASK_STEP_LANG", "Italian")
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": time.time(), "repo": repo, "source": "hook",
             "title": change.splitlines()[0][:100], "path": str(path), "how": "automatic (PreToolUse hook)"}  # fmt: skip
    t0 = time.monotonic()
    try:
        text = narrate._call(narrate.EXPLAINER, SYSTEM.format(lang=lang), redact(facts), timeout=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the work must never be blocked by the narrator
        _log({**entry, "narrator": "failed", "error": f"{type(exc).__name__}: {exc}"[:200]})
        return None
    entry.update(narrator="model", narrator_ms=round((time.monotonic() - t0) * 1000), announcement=text)
    _log(entry)
    return {"systemMessage": f"STEP · {Path(repo).name}\n{text}", "suppressOutput": True}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    try:
        answer = handle(event) if isinstance(event, dict) else None
    except Exception:  # noqa: BLE001 - a hook must exit 0 so the tool call proceeds
        answer = None
    if answer:
        print(json.dumps(answer, ensure_ascii=False))
    return 0
