"""Benchmark: MCP startup, tool latency, fixed token weight, engine pick time.

Spends nothing: no job is started and step() is never called. Tokens are a
rough estimate (bytes / 4), stated as such in the output.

    python bench/run.py [--reps 20] [--cmd subtask-mcp]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROTO = "2025-06-18"


def _send(proc: subprocess.Popen, msg: dict) -> None:
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, msg_id: int) -> dict:
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout")
        data = json.loads(line)
        if data.get("id") == msg_id:
            return data


def _session(cmd: list[str], env: dict, calls: list[tuple[str, dict]]) -> dict:
    """One server lifetime: startup time, tools/list, then each call timed."""
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env
    )
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTO,
                    "capabilities": {},
                    "clientInfo": {"name": "bench", "version": "0"},
                },
            },
        )
        init = _recv(proc, 1)
        startup = time.perf_counter() - t0
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = _recv(proc, 2)["result"]["tools"]
        timings, sizes = {}, {}
        for i, (name, args) in enumerate(calls, start=3):
            t = time.perf_counter()
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                },
            )
            out = _recv(proc, i)
            timings[name] = time.perf_counter() - t
            text = "".join(c.get("text", "") for c in out.get("result", {}).get("content", []))
            sizes[name] = len(text.encode())
        fixed = len(json.dumps(tools).encode()) + len((init["result"].get("instructions") or "").encode())
        return {
            "startup": startup,
            "timings": timings,
            "sizes": sizes,
            "fixed_bytes": fixed,
            "tools": len(tools),
        }
    finally:
        proc.kill()
        proc.wait()


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"median_ms": round(statistics.median(ordered) * 1000, 2), "p95_ms": round(p95 * 1000, 2)}


def _pick_times(env: dict, reps: int) -> dict:
    """capacity.pick() in a fresh interpreter: first call (cold cache), then warm."""
    code = (
        "import time,json;from subtask import capacity as c;"
        "t=time.perf_counter();c.pick('code');cold=time.perf_counter()-t;w=[]\n"
        f"for _ in range({reps}):\n t=time.perf_counter();c.pick('code');w.append(time.perf_counter()-t)\n"
        "print(json.dumps({'cold':cold,'warm':w}))"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    return {"cold_ms": round(data["cold"] * 1000, 2), "warm": _stats(data["warm"])}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--cmd", default=shutil.which("subtask-mcp") or "subtask-mcp")
    args = ap.parse_args()

    home = tempfile.mkdtemp(prefix="subtask-bench-")
    env = {**os.environ, "SUBTASK_HOME": home}
    calls = [("status", {}), ("engines", {})]
    runs = [_session(args.cmd.split(), env, calls) for _ in range(args.reps)]
    shutil.rmtree(home, ignore_errors=True)

    home = tempfile.mkdtemp(prefix="subtask-bench-")
    pick = _pick_times({**os.environ, "SUBTASK_HOME": home}, args.reps)
    shutil.rmtree(home, ignore_errors=True)

    fixed = runs[0]["fixed_bytes"]
    report = {
        "commit": _commit(),
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cmd": args.cmd,
        "reps": args.reps,
        "startup": _stats([r["startup"] for r in runs]),
        "tools": {n: _stats([r["timings"][n] for r in runs]) for n, _ in calls},
        "reply_tokens_est": {n: runs[0]["sizes"][n] // 4 for n, _ in calls},
        "fixed_tokens_est": fixed // 4,
        "tool_count": runs[0]["tools"],
        "pick": pick,
        "note": "tokens = bytes/4, rough estimate",
    }
    RESULTS.mkdir(exist_ok=True)
    prev = sorted(RESULTS.glob("*.json"))
    path = RESULTS / f"{report['when'].replace(':', '')}-{report['commit']}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    if prev:
        old = json.loads(prev[-1].read_text())
        print(
            f"\nvs {prev[-1].name}: startup {old['startup']['median_ms']} -> "
            f"{report['startup']['median_ms']} ms · fixed tokens {old['fixed_tokens_est']} -> "
            f"{report['fixed_tokens_est']} · pick warm {old['pick']['warm']['median_ms']} -> "
            f"{report['pick']['warm']['median_ms']} ms · pick cold {old['pick']['cold_ms']} -> "
            f"{report['pick']['cold_ms']} ms"
        )


if __name__ == "__main__":
    main()
