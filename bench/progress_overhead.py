"""Offline microbenchmark of MCP progress and of the admission/ledger hot paths. Spends nothing.

1. A fake job of ~--job-s seconds through the real fastserver, waited on with and without a
   progressToken: notifications, bytes on the server's stdout, server CPU time (from
   /proc/<pid>/stat utime+stime, all threads), wall time of the wait.
2. Latency of fast calls (status, start) with and without a progressToken.
3. admission.count() and ledger.committed() latency with 0, 1k, 10k synthetic historical
   jobs / ledger rows in a temp SUBTASK_HOME (index present, and corrupt index -> rebuild).

    python bench/progress_overhead.py [--job-s 25] [--reps 15]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_fake

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
TICK = os.sysconf("SC_CLK_TCK")


def cpu_s(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICK  # utime, stime (fields 14, 15)


def _stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {"n": len(xs), "median_ms": round(statistics.median(xs) * 1000, 2),
            "p95_ms": round(xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))] * 1000, 2)}  # fmt: skip


def env_for(home: Path, job_s: float) -> dict:
    env = e2e_fake.fake_env(home, home, 0)
    env["SUBTASK_FAKE_SCRIPT"] = (
        f'echo \'{{"tool":"work","exit":0}}\'; sleep {job_s:g}; echo \'{{"say":"ok"}}\''
    )
    env["SUBTASK_PROGRESS_MIN_S"] = "2"  # production defaults
    env["SUBTASK_PROGRESS_HEARTBEAT_S"] = "10"
    return env


def long_job(job_s: float, token: bool) -> dict:
    home = Path(tempfile.mkdtemp(prefix="subtask-e2e-prog-"))
    client = e2e_fake.Client(env_for(home, job_s), progress=token)
    pid = client.proc.pid
    try:
        cpu0, bytes0 = cpu_s(pid), client.bytes_in
        out = client.call("start", task="sleep", engine="fake", cwd=str(home), readonly=True)
        job = e2e_fake._ids(out)[0]
        t0 = time.monotonic()
        waits = 0
        while True:
            client.call("wait", ids=[job], timeout=600)
            waits += 1
            if e2e_fake._state(client.call("status", id=job), job) in e2e_fake.FINAL:
                break
        return {
            "progress_token": token,
            "wall_s": round(time.monotonic() - t0, 2),
            "wait_calls": waits,
            "notifications": client.notifications,
            "stdout_bytes": client.bytes_in - bytes0,
            "server_cpu_s": round(cpu_s(pid) - cpu0, 3),
        }
    finally:
        client.close()
        e2e_fake_discard(home)


def fast_calls(reps: int, token: bool) -> dict:
    home = Path(tempfile.mkdtemp(prefix="subtask-e2e-prog-"))
    client = e2e_fake.Client(env_for(home, 0.2), progress=token)
    try:
        status, start = [], []
        for _ in range(reps):
            t = time.perf_counter()
            client.call("status")
            status.append(time.perf_counter() - t)
            t = time.perf_counter()
            out = client.call("start", task="quick", engine="fake", cwd=str(home), readonly=True)
            start.append(time.perf_counter() - t)
            job = e2e_fake._ids(out)
            if job:
                client.call("wait", ids=job, timeout=30)
        return {"progress_token": token, "status": _stats(status), "start": _stats(start)}
    finally:
        client.close()
        e2e_fake_discard(home)


HOT = r"""
import json, sys, time
from subtask import admission, ledger
reps = int(sys.argv[1]); out = {}
for name, fn in (("admission.count", admission.count), ("ledger.committed", ledger.committed)):
    xs = []
    for _ in range(reps):
        t = time.perf_counter(); fn(); xs.append(time.perf_counter() - t)
    out[name] = xs
print(json.dumps(out))
"""


def synth(home: Path, n: int) -> None:
    """n finished jobs and n settled reservations (2n ledger rows) this month."""
    jobs = home / "jobs"
    jobs.mkdir(parents=True)
    for i in range(n):
        d = jobs / f"st-{i:010x}"
        d.mkdir()
        (d / "status.json").write_text(json.dumps({"v": 1, "state": "done", "updated": time.time()}))
    ledger = home / "ledger"
    ledger.mkdir()
    month = time.strftime("%Y-%m", time.gmtime())
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with (ledger / f"{month}.jsonl").open("w") as fh:
        for i in range(n):
            rid = f"st-{i:010x}#1"
            fh.write(
                json.dumps(
                    {
                        "v": 2,
                        "ts": ts,
                        "job": rid[:-2],
                        "rid": rid,
                        "op": "reserve",
                        "usd": 1.0,
                        "month": month,
                    }
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "v": 2,
                        "ts": ts,
                        "job": rid[:-2],
                        "rid": rid,
                        "op": "settle",
                        "usd": 0.5,
                        "month": month,
                    }
                )
                + "\n"
            )


def hot_paths(sizes: list[int], reps: int) -> list[dict]:
    rows = []
    for n in sizes:
        for index in ("present", "corrupt"):
            home = Path(tempfile.mkdtemp(prefix="subtask-e2e-hot-"))
            synth(home, n)
            env = {**os.environ, "SUBTASK_HOME": str(home), "PYTHONPATH": str(REPO)}
            if index == "present":
                (home / "active.json").write_text(json.dumps({"v": 1, "jobs": {}}))
                code_reps = reps
            else:  # a missing index means "no active jobs"; a corrupt one forces a rebuild from job folders
                (home / "active.json").write_text("{")
                code_reps = 1  # the first call rebuilds and writes the index; measure that one call
            done = subprocess.run([sys.executable, "-c", HOT, str(code_reps)], env=env, capture_output=True,
                                  text=True, check=True, cwd=REPO)  # fmt: skip
            data = json.loads(done.stdout)
            rows.append({"history": n, "ledger_rows": 2 * n, "active_index": index,
                         **{k: _stats(v) for k, v in data.items()}})  # fmt: skip
            e2e_fake_discard(home)
    return rows


def e2e_fake_discard(home: Path) -> None:
    import shutil

    if home.name.startswith("subtask-e2e-") and str(home).startswith(tempfile.gettempdir()):
        shutil.rmtree(home, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-s", type=float, default=25)
    ap.add_argument("--reps", type=int, default=15)
    args = ap.parse_args()
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip()  # fmt: skip
    report = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": commit,
        "job_s": args.job_s,
        "progress_settings": {"min_interval_s": 2, "heartbeat_s": 10},
        "long_job": [long_job(args.job_s, True), long_job(args.job_s, False)],
        "fast_calls": [fast_calls(args.reps, True), fast_calls(args.reps, False)],
        "hot_paths": hot_paths([0, 1000, 10000], args.reps),
        "note": "offline, fake engine; server CPU from /proc/<pid>/stat (utime+stime, all threads)",
    }
    out = BENCH / "results" / f"progress-overhead-{report['when'].replace(':', '')}-{commit}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
