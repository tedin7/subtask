"""Client-side health check: app present, entry configured, server answers over stdio."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .clients import Client, InstallError

HANDSHAKE_TIMEOUT_S = 10.0
PROTOCOL = "2025-06-18"


def _read_reply(proc: subprocess.Popen[bytes], want_id: int, deadline: float) -> dict[str, Any]:
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    buf = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("no reply within the time limit")
        if not sel.select(left):
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            raise ConnectionError(f"server exited (code {proc.poll()})")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg


def handshake(
    argv: list[str], env: dict[str, str] | None = None, timeout: float = HANDSHAKE_TIMEOUT_S
) -> dict:
    """Spawn the server, send initialize + tools/list, return server name and tool count."""
    deadline = time.monotonic() + timeout
    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=full_env)  # fmt: skip
    except OSError as exc:
        return {"ok": False, "error": f"cannot start: {exc}"}
    assert proc.stdin is not None
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "subtask-doctor", "version": "1"}}}  # fmt: skip
        proc.stdin.write((json.dumps(init) + "\n").encode())
        proc.stdin.flush()
        r1 = _read_reply(proc, 1, deadline)
        if "error" in r1:
            return {"ok": False, "error": f"initialize: {r1['error'].get('message')}"}
        notes = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        proc.stdin.write((json.dumps(notes) + "\n" + json.dumps(listing) + "\n").encode())
        proc.stdin.flush()
        r2 = _read_reply(proc, 2, deadline)
        tools = (r2.get("result") or {}).get("tools") or []
        info = (r1.get("result") or {}).get("serverInfo") or {}
        return {"ok": bool(tools), "server": info.get("name"), "version": info.get("version"),
                "protocol": (r1.get("result") or {}).get("protocolVersion"), "tools": len(tools)}  # fmt: skip
    except (TimeoutError, ConnectionError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def doctor(c: Client, scope: str) -> dict[str, Any]:
    exe = c.exe()
    report: dict[str, Any] = {"client": c.name, "scope": scope, "present": bool(exe), "binary": exe,
                              "version": c.version() if exe else None}  # fmt: skip
    try:
        c.check_scope(scope)
        path = c.config_path(scope)
        entry = c.read_entry(scope)
    except InstallError as exc:
        return {**report, "configured": False, "connects": False, "error": str(exc)}
    argv = Client.target(entry)
    report |= {"config": str(path), "configured": bool(argv), "command": argv}
    if not argv:
        return {**report, "connects": False, "auth": "not needed"}
    launcher = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
    report["command_exists"] = bool(launcher and Path(launcher).is_file() and os.access(launcher, os.X_OK))
    env = (entry or {}).get("env") or {}
    hs = handshake(argv, {str(k): str(v) for k, v in env.items()}) if report["command_exists"] else {
        "ok": False, "error": "command not found or not executable"}  # fmt: skip
    # The subtask server needs no credentials to connect; engines read theirs only when a job runs.
    return {**report, "connects": hs.pop("ok"), "handshake": hs, "auth": "not needed"}
