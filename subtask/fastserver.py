"""MCP server (stdio) without the mcp library: JSON-RPC by hand, ~10x faster start.

Dual-era (MCP 2026-07-28 "Versioning and Compatibility"):
- modern requests carry `_meta["io.modelcontextprotocol/protocolVersion"]` and
  are served statelessly, with `resultType` and `serverInfo` in every result;
  `server/discover` answers; unknown versions get UnsupportedProtocolVersion (-32022);
- legacy clients open with `initialize` (2025-11-25 and earlier) and keep the
  negotiated revision for this process.

Progress: a tools/call with `_meta.progressToken` gets `notifications/progress`
while it runs (see progress.py). `notifications/cancelled` ends the call: no
further message is sent for it, and the jobs keep running (stop() stops work).

Importing the mcp package costs ~560 ms; this file answers before `core` is
even imported. Tool names, descriptions and schemas come from tools.json,
generated from server.py (`subtask-mcp-fastmcp --dump-tools`).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from . import __version__

SPEC = json.loads((Path(__file__).with_name("tools.json")).read_text())
MODERN = ("2026-07-28",)
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSIONS = MODERN + LEGACY
SERVER_INFO = {"name": "subtask", "version": __version__}
META = "io.modelcontextprotocol/"
WAIT_TICK_S = 0.5
CACHE = {"ttlMs": 3_600_000, "cacheScope": "public"}  # MUST on discover and tools/list (2026-07-28)

_out = threading.Lock()
_pending: list[threading.Thread] = []  # calls still running when the client closes stdin
_core_lock = threading.Lock()  # quick state-changing tools run one at a time in this process
_cancelled: dict[object, threading.Event] = {}
_state: dict[str, str | None] = {"legacy": None}  # protocol version chosen by `initialize`


def _send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _out:  # one writer: lines never interleave
        sys.stdout.write(line)
        sys.stdout.flush()


class Call:
    """One in-flight tools/call: its id, progress token, cancellation and era."""

    def __init__(self, msg_id: object, token: object, modern: bool) -> None:
        self.id, self.token, self.modern = msg_id, token, modern
        self.cancel = _cancelled.setdefault(msg_id, threading.Event())
        self.reporter = None
        if isinstance(token, (str, int)) and not isinstance(token, bool):
            from .progress import Reporter

            self.reporter = Reporter(self.progress)

    def progress(self, value: float, message: str, total: float | None) -> None:
        if self.cancel.is_set():
            return  # MUST NOT send anything for a cancelled request
        params: dict = {"progressToken": self.token, "progress": value, "message": message}
        if total is not None:
            params["total"] = total
        _send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

    def sleep(self, seconds: float) -> bool:
        """Sleep, polling progress; True when cancelled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel.wait(min(WAIT_TICK_S, max(0.0, end - time.monotonic()))):
                return True
            if self.reporter:
                self.reporter.poll()
        return self.cancel.is_set()


def _wait(
    core,
    call: Call,
    ids: list[str],
    timeout: int = 300,
    any: bool = False,
    since: str | None = None,
    mode: str | None = None,
    stall_timeout: float | None = None,
) -> str:
    deadline = time.monotonic() + max(1, min(int(timeout), 600))
    if call.reporter:
        for job_id in ids:
            if core.known(job_id):
                call.reporter.add(job_id)
        call.reporter.poll()
    first = True
    while True:
        out = core.wait(ids, 0, any, since=since, count=first, mode=mode, stall_timeout=stall_timeout)
        first = False
        if not out.startswith("still running") or time.monotonic() >= deadline:
            if call.reporter:
                call.reporter.poll()
            return out
        if call.sleep(1.0):
            return "wait cancelled; jobs keep running"


def _call(name: str, args: dict, call: Call) -> str:
    from . import core

    if name == "wait":
        return _wait(core, call, **args)
    if name == "stop":
        if call.reporter:
            call.reporter.add(args["id"])
            call.reporter.phase(f"stopping {args['id']}", force=True)
        return core.stop(args["id"])
    if name == "step":
        a = {"decisions": None, "repo": None, **args}
        return core.step(a["title"], a["path"], a["how"], a["decisions"], a["repo"])
    if name == "result":
        return core.result(args["id"], args.get("full", False), args.get("evidence", False))
    if name == "inspect":
        return core.inspect(args["id"], args.get("tail", 80), args.get("grep"))
    if name == "steer":
        return core.steer(args["id"], args["action"], args.get("instruction"))
    if name == "engines":
        return core.engines() + "\n" + core.spend()
    with _core_lock:
        if name == "start":
            return core.start(**_start_args(args), progress=call.reporter)
        if name == "start_many":
            return core.start_many(args["tasks"], progress=call.reporter)
        if name == "status":
            return core.status(args.get("id"))
        if name == "reply":
            return core.reply(args["id"], args["message"])
        if name == "cleanup":
            return core.cleanup(args["id"])
    raise KeyError(name)


START_ARGS = {
    "task", "kind", "repo", "cwd", "files", "readonly", "engine", "max_usd", "effort",
    "base", "max_turns", "policy", "verify",
}  # fmt: skip


def _start_args(args: dict) -> dict:
    unknown = set(args) - START_ARGS
    if unknown:
        raise TypeError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
    if "task" not in args:
        raise KeyError("task")
    return args


def _with_meta(modern: bool, payload: dict) -> dict:
    if modern:
        return {"resultType": "complete", **payload, "_meta": {META + "serverInfo": SERVER_INFO}}
    return payload


def _tool_call(msg_id: object, params: dict, modern: bool) -> None:
    meta = params.get("_meta") or {}
    call = Call(msg_id, meta.get("progressToken"), modern)
    name, args = params.get("name", ""), params.get("arguments") or {}
    try:
        text, error = _call(name, args, call), False
    except KeyError as exc:
        text, error = f"unknown tool or missing argument: {exc}", True
    except TypeError as exc:
        text, error = f"bad arguments: {exc}", True
    except Exception as exc:  # noqa: BLE001 - a tool error is a result, never a crash
        text, error = f"{type(exc).__name__}: {exc}", True
    finally:
        _cancelled.pop(msg_id, None)
    if call.cancel.is_set():
        return  # cancelled: no response, no notification
    from .redact import redact

    payload = {"content": [{"type": "text", "text": redact(text)}], "isError": error}
    _send({"jsonrpc": "2.0", "id": msg_id, "result": _with_meta(modern, payload)})


def _error(msg_id: object, code: int, message: str, data: object = None) -> None:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _send({"jsonrpc": "2.0", "id": msg_id, "error": err})


def _era(msg: dict) -> tuple[bool | None, str | None]:
    """(modern?, problem). Modern when the request declares a version in _meta."""
    meta = (msg.get("params") or {}).get("_meta") or {}
    version = meta.get(META + "protocolVersion")
    if version is None:
        return False, None
    if version not in VERSIONS:
        return None, str(version)
    if version not in MODERN:
        return False, None  # a legacy version named per request: serve it as legacy
    if META + "clientCapabilities" not in meta:
        return None, "missing-capabilities"
    return True, None


def _handle(msg: dict) -> None:
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:
        if method == "notifications/cancelled":
            event = _cancelled.get((msg.get("params") or {}).get("requestId"))
            if event:
                event.set()
        return  # other notifications are never answered
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        chosen = asked if asked in LEGACY else LEGACY[0]
        _state["legacy"] = chosen
        result = {
            "protocolVersion": chosen,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": SPEC["instructions"],
        }
        _send({"jsonrpc": "2.0", "id": msg_id, "result": result})
        return
    modern, problem = _era(msg)
    if modern is None:
        if problem == "missing-capabilities":
            _error(msg_id, -32602, "Invalid params: missing _meta io.modelcontextprotocol/clientCapabilities")
        else:
            _error(
                msg_id,
                -32022,
                "Unsupported protocol version",
                {"supported": list(VERSIONS), "requested": problem},
            )
        return
    if method == "server/discover":
        result = {
            "supportedVersions": list(VERSIONS),
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": SPEC["instructions"],
            **CACHE,
        }
        _send({"jsonrpc": "2.0", "id": msg_id, "result": _with_meta(True, result)})
    elif method == "tools/list":
        listing = {"tools": SPEC["tools"], **(CACHE if modern else {})}
        _send({"jsonrpc": "2.0", "id": msg_id, "result": _with_meta(modern, listing)})
    elif method == "tools/call":
        _cancelled.setdefault(msg_id, threading.Event())  # a cancel may arrive before the thread runs
        worker = threading.Thread(
            target=_tool_call, args=(msg_id, msg.get("params") or {}, modern), daemon=True
        )
        worker.start()
        _pending.append(worker)
    elif method == "ping":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": _with_meta(modern, {})})
    else:
        _error(msg_id, -32601, f"no method {method}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        if isinstance(msg, dict):
            _handle(msg)
        else:
            _error(None, -32600, "invalid request")
    for worker in _pending:
        worker.join(timeout=30)  # stdin closed: still answer the calls already received


if __name__ == "__main__":
    main()
