"""MCP server (stdio) without the mcp library: JSON-RPC by hand, ~10x faster start.

Importing the mcp package costs ~560 ms; this file answers `initialize` before
`core` is even imported. Tool names, descriptions and schemas come from
tools.json, which a test keeps equal to what server.py (FastMCP) would publish.
server.py stays as the reference implementation and fallback.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

SPEC = json.loads((Path(__file__).with_name("tools.json")).read_text())
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_out = threading.Lock()
_pending: list[threading.Thread] = []  # calls still running when the client closes stdin
_core_lock = threading.Lock()  # FastMCP ran plain tools one at a time: keep that


def _send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    with _out:
        sys.stdout.write(line)
        sys.stdout.flush()


def _wait(core, ids: list[str], timeout: int = 300, any: bool = False) -> str:
    deadline = time.monotonic() + max(1, min(timeout, 600))
    while True:
        with _core_lock:
            out = core.wait(ids, 0, any)
        if not out.startswith("still running") or time.monotonic() >= deadline:
            return out
        time.sleep(1)


def _call(name: str, args: dict) -> str:
    from . import core

    if name == "wait":
        return _wait(core, **args)
    if name == "stop":
        return core.stop(args["id"])
    if name == "step":
        a = {"decisions": None, "repo": None, **args}
        return core.step(a["title"], a["path"], a["how"], a["decisions"], a["repo"])
    with _core_lock:
        if name == "start":
            a = {
                "kind": "code",
                "repo": None,
                "cwd": None,
                "files": None,
                "readonly": False,
                "engine": "auto",
                "max_usd": None,
                "effort": "medium",
                "base": "HEAD",
                "max_turns": 60,
                **args,
            }
            return core.start(
                a["task"],
                a["engine"],
                a["repo"],
                a["cwd"],
                a["base"],
                a["readonly"],
                a["max_usd"],
                a["max_turns"],
                a["effort"],
                kind=a["kind"],
                files=a["files"],
            )
        if name == "start_many":
            return core.start_many(args["tasks"])
        if name == "status":
            return core.status(args.get("id"))
        if name == "result":
            return core.result(args["id"], args.get("full", False))
        if name == "reply":
            return core.reply(args["id"], args["message"])
        if name == "cleanup":
            return core.cleanup(args["id"])
        if name == "engines":
            return core.engines() + "\n" + core.spend()
    raise KeyError(name)


def _tool_call(msg_id: object, params: dict) -> None:
    name, args = params.get("name", ""), params.get("arguments") or {}
    try:
        text, error = _call(name, args), False
    except KeyError as exc:
        text, error = f"unknown tool or missing argument: {exc}", True
    except TypeError as exc:
        text, error = f"bad arguments: {exc}", True
    except Exception as exc:  # noqa: BLE001 - a tool error is a result, never a crash
        text, error = f"{type(exc).__name__}: {exc}", True
    _send(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": error},
        }
    )


def _handle(msg: dict) -> None:
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:
        return  # notification: never answered
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        _send(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "subtask", "version": "0.2.0"},
                    "instructions": SPEC["instructions"],
                },
            }
        )
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": SPEC["tools"]}})
    elif method == "tools/call":
        worker = threading.Thread(target=_tool_call, args=(msg_id, msg.get("params") or {}), daemon=True)
        worker.start()
        _pending.append(worker)
    elif method == "ping":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
    else:
        _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"no method {method}"}})


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if isinstance(msg, dict):
            _handle(msg)
    for worker in _pending:
        worker.join(timeout=30)  # stdin closed: still answer the calls already received


if __name__ == "__main__":
    main()
