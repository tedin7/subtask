"""Offline driver for bench/e2e.py --fake: a scripted parent talks MCP to the real fastserver,
which runs jobs on the test-only fake engine (tests/fake_engine.py). No model, no network.

The fake engine's script plays a worker with a scripted outcome (success, fail_then_success,
fail): on success it applies the reference patch or says the reference answer. This exercises
the whole pipeline (admission, worktrees, wait/status, result, reply, acceptance, regressions,
records), not the quality of any model. Results are labelled smoke.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
MARK = re.compile(r"\[\[bench-fake task=(\w+) outcome=(\w+)\]\]")
FINAL = ("done", "failed", "stopped")

SCRIPT = r"""
STATE="$(git rev-parse --git-dir 2>/dev/null || echo .)/bench-fake-state"
case "$1" in
  *"[[bench-fake "*) printf '%s\n' "$1" | grep -o 'task=[a-z0-9]* outcome=[a-z_]*' > "$STATE"; echo 0 >> "$STATE";;
esac
TASK=$(head -1 "$STATE" | sed 's/task=\([a-z0-9]*\).*/\1/')
OUTCOME=$(head -1 "$STATE" | sed 's/.*outcome=//')
N=$(tail -1 "$STATE"); N=$((N+1)); head -1 "$STATE" > "$STATE.tmp"; echo $N >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"
OK=no
[ "$OUTCOME" = success ] && OK=yes
[ "$OUTCOME" = fail_then_success ] && [ $N -ge 2 ] && OK=yes
echo '{"tool":"read files","exit":0}'
sleep @SLEEP@
if [ $OK = yes ]; then
  if [ -f "@DATA@/$TASK.patch" ]; then
    git apply "@DATA@/$TASK.patch" && git -c user.name=fake -c user.email=fake@bench commit -qam "fake: $TASK" >/dev/null
    echo '{"tool":"git commit","exit":0}'
  fi
  cat "@DATA@/$TASK.ok.json"
else
  cat "@DATA@/$TASK.bad.json"
fi
"""


def prepare_data(tasks: list[dict], folder: Path) -> None:
    """Per task: the reference patch (code/hard) and the JSON lines the fake worker prints."""
    for task in tasks:
        acc = task["acceptance"]
        if acc["type"] == "hidden_test":
            shutil.copy2(REPO / acc["reference_patch"], folder / f"{task['id']}.patch")
            ok = f"Applied the fix for {task['id']}.\n\n## Summary\nfixed {task['id']}; 1 commit"
        else:
            ok = acc["reference"] + "\n\n## Summary\n" + acc["reference"]
        bad = "I looked but could not finish.\n\n## Summary\nnot done"
        (folder / f"{task['id']}.ok.json").write_text(json.dumps({"say": ok}) + "\n")
        (folder / f"{task['id']}.bad.json").write_text(json.dumps({"say": bad}) + "\n")


def fake_env(home: Path, data: Path, sleep_s: float) -> dict:
    """Environment for the subtask server: private home, no keys, no real engine reachable."""
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in ("git", "sh", "sed", "grep", "head", "tail", "cat", "mv", "sleep", "printf", "echo"):
        found = shutil.which(tool)
        if found and not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    script = SCRIPT.replace("@DATA@", str(data)).replace("@SLEEP@", f"{sleep_s:g}")
    return {
        "PATH": str(bin_dir),
        "HOME": str(home / "userhome"),
        "LANG": "C.UTF-8",
        "SUBTASK_HOME": str(home / "subtask"),
        "SUBTASK_ENV_FILE": os.devnull,
        "SUBTASK_DSH_KEY_FILE": os.devnull,
        "SUBTASK_ROUTING": str(home / "routing.json"),
        "SUBTASK_ENGINE_PLUGINS": "tests.fake_engine:adapter",
        "SUBTASK_FAKE_SCRIPT": script,
        "SUBTASK_PROGRESS_MIN_S": "0.5",
        "SUBTASK_PROGRESS_HEARTBEAT_S": "2",
        "PYTHONPATH": str(REPO),
    }


class Client:
    """Minimal MCP stdio client over the real fastserver; counts what the parent would read."""

    def __init__(self, env: dict, progress: bool):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "subtask.fastserver"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
            cwd=REPO,
        )
        self.progress = progress
        self.next_id = 0
        self.calls: list[dict] = []
        self.notifications = 0
        self.bytes_in = 0
        self._rpc(
            "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e"}}
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("subtask server closed stdout")
            self.bytes_in += len(line.encode())
            data = json.loads(line)
            if data.get("method") == "notifications/progress":
                self.notifications += 1
                continue
            if data.get("id") == self.next_id:
                return data

    def call(self, name: str, **args) -> str:
        params: dict = {"name": name, "arguments": args}
        if self.progress:
            params["_meta"] = {"progressToken": f"p{self.next_id + 1}"}
        t0 = time.monotonic()
        out = self._rpc("tools/call", params)
        text = "".join(c.get("text", "") for c in (out.get("result") or {}).get("content", []))
        self.calls.append({"tool": name, "s": round(time.monotonic() - t0, 3), "bytes": len(text.encode())})
        return text

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()


def _ids(text: str) -> list[str]:
    return re.findall(r"\b(st-[0-9a-f]{10})\b started", text)


def _state(line: str, job: str) -> str:
    m = re.search(rf"{job} (\w+)", line)
    return m.group(1) if m else "unknown"


def drive(client: Client, task: dict, repo: Path, outcome: str, abl: dict, max_retries: int, check) -> dict:
    """Scripted parent: delegate, wait, read, verify with `check()`, reply once on failure."""
    readonly = task["kind"] in ("read", "review")
    item = {
        "task": f"{task['prompt']}\n\n[[bench-fake task={task['id']} outcome={outcome}]]",
        "engine": "fake",
        "kind": task["kind"],
        "repo": str(repo),
        "readonly": readonly,
        "max_usd": task["budget"]["max_usd"],
    }
    if abl["batching"] == "on":
        started = client.call("start_many", tasks=[item])
    else:
        started = client.call("start", **item)
    ids = _ids(started)
    if not ids:
        return {"error": f"not started: {started[:200]}", "jobs": [], "replies": 0, "attempt_ok": []}
    job = ids[0]
    replies, attempt_ok, answer = 0, [], ""
    while True:
        _finish(client, job, abl["batching"], task["budget"]["max_minutes"] * 60)
        answer = client.call("result", id=job, full=abl["summary"] == "full")
        ok = check(job, answer)
        attempt_ok.append(ok)
        if ok or replies >= max_retries:
            break
        client.call("reply", id=job, message="The acceptance check failed. Fix it and try again.")
        replies += 1
    return {"jobs": [job], "replies": replies, "attempt_ok": attempt_ok, "answer": answer}


def _finish(client: Client, job: str, batching: str, timeout_s: float) -> None:
    """batching on: wait() (it may return early with deltas); off: status() polling every second."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if batching == "on":
            client.call("wait", ids=[job], timeout=int(min(600, max(1, deadline - time.monotonic()))))
        else:
            time.sleep(1)
        if _state(client.call("status", id=job), job) in FINAL:
            return


def auto_choice(env: dict, kind: str) -> str:
    """What engine='auto' would pick for this kind in the sandboxed env (no job started)."""
    code = f"from subtask import router; print(router.explain({kind!r}).splitlines()[0])"
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return (done.stdout.strip() or done.stderr.strip())[:300]


def new_home() -> Path:
    return Path(tempfile.mkdtemp(prefix="subtask-e2e-fake-"))
