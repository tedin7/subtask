"""R03, R23: dual-era MCP, progress notifications while a tool runs, cancellation.

The server runs as a real subprocess; a small client reads stdout line by line
with timestamps, so "during the call" is measured, not assumed.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

from .conftest import fake_cli

MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
}


class Client:
    def __init__(self, env: dict) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "subtask.fastserver"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.lines: queue.Queue = queue.Queue()
        self.raw: list[str] = []
        self.early: dict = {}  # replies that arrived while waiting for another id
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.raw.append(line)
            self.lines.put((time.monotonic(), json.loads(line)))

    def send(self, msg: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def until(self, msg_id, timeout: float = 30) -> tuple[dict, list[tuple[float, dict]]]:
        if msg_id in self.early:
            return self.early.pop(msg_id), []
        seen = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                t, msg = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append((t, msg))
            if "result" in msg or "error" in msg:
                if msg.get("id") == msg_id:
                    return msg, seen
                self.early[msg.get("id")] = msg
        raise AssertionError(f"no reply to {msg_id}; seen {seen}")

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _env() -> dict:
    return {**os.environ, "SUBTASK_PROGRESS_MIN_S": "0.5", "SUBTASK_PROGRESS_HEARTBEAT_S": "2"}


def test_modern_discover_and_stateless_calls(home):
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": MODERN}})
    disc, _ = c.until(1)
    assert disc["result"]["resultType"] == "complete"
    assert "2026-07-28" in disc["result"]["supportedVersions"]
    assert disc["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "subtask"
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": MODERN}})
    tools, _ = c.until(2)
    assert tools["result"]["resultType"] == "complete" and len(tools["result"]["tools"]) == 10
    for result in (disc["result"], tools["result"]):
        assert result["ttlMs"] >= 0 and result["cacheScope"] == "public"
    c.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "status", "arguments": {}, "_meta": MODERN}})  # fmt: skip
    status, _ = c.until(3)
    assert status["result"]["resultType"] == "complete" and status["result"]["isError"] is False
    c.close()


def test_modern_errors(home):
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
            "params": {"_meta": {**MODERN, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}}})  # fmt: skip
    bad, _ = c.until(1)
    assert bad["error"]["code"] == -32022
    assert (
        bad["error"]["data"]["requested"] == "1900-01-01"
        and "2026-07-28" in bad["error"]["data"]["supported"]
    )
    meta = {k: v for k, v in MODERN.items() if "Capabilities" not in k}
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": meta}})
    missing, _ = c.until(2)
    assert missing["error"]["code"] == -32602
    c.close()


def test_legacy_handshake_still_works(home):
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    init, _ = c.until(1)
    assert init["result"]["protocolVersion"] == "2025-06-18"
    import subtask

    assert init["result"]["serverInfo"]["version"] == subtask.__version__
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools, _ = c.until(2)
    assert "resultType" not in tools["result"]
    c.send({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
    assert c.until(3)[0]["result"]["protocolVersion"] == "2025-11-25"
    c.close()


def test_progress_arrives_during_wait_and_stdout_stays_json(home):
    fake_cli(home, "kimi", "sleep 1; echo working; sleep 4; echo done")
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "start", "arguments": {"task": "x", "engine": "kimi", "cwd": str(home)}}})  # fmt: skip
    started, _ = c.until(1)
    job = started["result"]["content"][0]["text"].split()[0]
    t0 = time.monotonic()
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "wait", "arguments": {"ids": [job], "timeout": 60},
                       "_meta": {**MODERN, "progressToken": "tok-1"}}})  # fmt: skip
    reply, seen = c.until(2, timeout=60)
    t_end = next(t for t, m in seen if m.get("id") == 2)
    notes = [(t, m["params"]) for t, m in seen if m.get("method") == "notifications/progress"]
    assert notes, "no progress notification"
    assert all(p["progressToken"] == "tok-1" for _, p in notes)
    values = [p["progress"] for _, p in notes]
    assert values == sorted(values) and len(set(values)) == len(values)  # strictly increasing
    assert all(t < t_end for t, _ in notes)  # during the call, not after
    assert any(t - t0 < 3.5 for t, _ in notes)  # early, not only at the end
    assert all("Subtask ·" in p["message"] and job in p["message"] for _, p in notes)
    assert all("total" not in p for _, p in notes)  # no invented percentage
    assert reply["result"]["isError"] is False
    for line in c.raw:
        json.loads(line)  # every stdout line is a JSON-RPC message
        assert "\x1b[" not in line
    c.close()


def test_no_token_no_notifications(home):
    fake_cli(home, "kimi", "sleep 2; echo done")
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "start", "arguments": {"task": "x", "engine": "kimi", "cwd": str(home)}}})  # fmt: skip
    job = c.until(1)[0]["result"]["content"][0]["text"].split()[0]
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "wait", "arguments": {"ids": [job], "timeout": 30}}})  # fmt: skip
    _, seen = c.until(2, timeout=40)
    assert not [m for _, m in seen if m.get("method") == "notifications/progress"]
    c.close()


def test_cancel_ends_the_wait_not_the_job(home):
    fake_cli(home, "kimi", "sleep 6; echo done")
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "start", "arguments": {"task": "x", "engine": "kimi", "cwd": str(home)}}})  # fmt: skip
    job = c.until(1)[0]["result"]["content"][0]["text"].split()[0]
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "wait", "arguments": {"ids": [job], "timeout": 60}, "_meta": {"progressToken": 7}}})  # fmt: skip
    time.sleep(1.5)
    c.send(
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2, "reason": "user"}}
    )
    t_cancel = time.monotonic()
    c.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    c.until(3)
    time.sleep(2)
    after = []
    while not c.lines.empty():
        t, m = c.lines.get()
        if t > t_cancel + 1.2:
            after.append(m)
    assert not [m for m in after if m.get("id") == 2 or m.get("params", {}).get("progressToken") == 7]
    from subtask import jobs

    assert jobs.status(job)["state"] in ("queued", "running")  # the work goes on
    from .conftest import wait_final

    assert wait_final(job, 20)["state"] == "done"
    c.close()


def test_server_keeps_answering_while_a_wait_blocks(home):
    fake_cli(home, "kimi", "sleep 5; echo done")
    c = Client(_env())
    c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "start", "arguments": {"task": "x", "engine": "kimi", "cwd": str(home)}}})  # fmt: skip
    job = c.until(1)[0]["result"]["content"][0]["text"].split()[0]
    c.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "wait", "arguments": {"ids": [job], "timeout": 60}}})  # fmt: skip
    t = time.monotonic()
    c.send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "stop", "arguments": {"id": job}},
        }
    )
    stopped, _ = c.until(3, timeout=20)
    assert "stopped" in stopped["result"]["content"][0]["text"]
    assert time.monotonic() - t < 15
    waited, _ = c.until(2, timeout=20)
    assert "stopped" in waited["result"]["content"][0]["text"]
    c.close()


def test_wait_cursor_fallback_reports_changes(home):
    from subtask import core

    fake_cli(home, "kimi", "sleep 4; echo done")
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    first = core.wait([job], 1, since="")
    assert first.startswith("still running") and "cursor=" in first
    cursor = first.rsplit("cursor=", 1)[1]
    second = core.wait([job], 1, since=cursor)
    assert "cursor=" in second and "admission" not in second  # old events are not repeated


def test_reporter_follows_the_latest_change_and_admits_missing_detail(home):
    from subtask import core, events, progress

    fake_cli(home, "kimi", "sleep 8")
    a = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    b = core.start("y", engine="kimi", cwd=str(home)).split()[0]
    clock = [0.0]
    sent: list[str] = []
    rep = progress.Reporter(lambda v, m, t: sent.append(m), [a, b], now=lambda: clock[0], min_interval=0)
    time.sleep(0.5)
    rep.poll()
    events.emit(a, "tool", "running tests: pytest -q", source="adapter")
    clock[0] = 3
    rep.poll()
    assert sent[-1].endswith(f"{a} running tests: pytest -q")
    clock[0] = 20
    rep.poll()
    assert progress.NO_DETAIL not in rep._what(a)
    assert rep._what(b) == progress.NO_DETAIL  # kimi plain output: no structured activity
    core.stop(a)
    core.stop(b)
