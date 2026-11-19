"""T-1002921: compact context with recoverable evidence, measured on real long sessions.

Offline and free (no model call, cost 0 USD). Participants are the N longest real job sessions
(`output-1.jsonl` of opencode-go and codex jobs), the same for every arm. Each session is read as
a list of tool calls (command, output). The evidence to recover is the exact output of its failing
calls (a fixed rule, no randomness). Three ways to carry the session are compared:

  full     the whole history: every call with its full output
  summary  what a typical rolling summary keeps: the last K calls and the first K, outputs clipped
  state    one short line per call (index, tool, command head, status, output length, sha256 prefix)
           plus a pointer `job:LINE` that resolves to the stored output; nothing else is kept

Measured per arm: tokens carried (chars / 4), exact evidence recovered (the output bytes match the
stored sha256), tokens per recovered proof, and the extra tokens a recovery costs. A recovery
through a pointer is real: the proof is read back from the file and checked against the hash.
Note: chars / 4 underestimates real sessions by about 2.3x (191k real vs 84k estimated), the same for
every arm, so compare arms by ratio, not by absolute tokens (docs/proposals-comparison-20261119.md).
What is NOT measured: whether a model solves the task better with one arm (that needs model runs).

Run: python bench/context_compaction.py [--jobs N] [--json]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
from pathlib import Path

JOBS = Path(os.environ.get("SUBTASK_HOME", Path.home() / ".local/share/subtask")) / "jobs"
FAIL = re.compile(
    r"(Traceback|\bFAILED\b|\bERROR\b|exit code [1-9]|exited [1-9]|No such file|command not found)"
)
CLIP = 300  # chars kept per output by the summary arm
KEEP = 8  # calls kept at each end by the summary arm


def calls_of(path: str) -> list[dict]:
    out: list[dict] = []
    for line_no, line in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "tool_use":
            st = d["part"].get("state", {})
            cmd = st.get("input", {}).get("command") or json.dumps(st.get("input", {}))[:200]
            out.append({"line": line_no, "tool": d["part"].get("tool"), "cmd": str(cmd),
                        "out": str(st.get("output", "")), "ok": st.get("status") == "completed"})  # fmt: skip
        elif d.get("type") == "item.completed" and d["item"].get("type") == "command_execution":
            it = d["item"]
            out.append({"line": line_no, "tool": "shell", "cmd": str(it.get("command", "")),
                        "out": str(it.get("aggregated_output", "")), "ok": it.get("exit_code") in (0, None)})  # fmt: skip
    return out


def is_proof(c: dict) -> bool:
    return (not c["ok"]) or bool(FAIL.search(c["out"]))


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def tok(text: str) -> int:
    return len(text) // 4


def render_full(calls: list[dict]) -> str:
    return "\n".join(f"#{i} {c['tool']} $ {c['cmd']}\n{c['out']}" for i, c in enumerate(calls))


def render_summary(calls: list[dict]) -> str:
    idx = sorted(set(range(min(KEEP, len(calls)))) | set(range(max(len(calls) - KEEP, 0), len(calls))))
    return "\n".join(
        f"#{i} {calls[i]['tool']} $ {calls[i]['cmd'][:120]}\n{calls[i]['out'][:CLIP]}" for i in idx
    )


def render_state(calls: list[dict], job: str) -> str:
    return "\n".join(
        f"#{i} {c['tool']} $ {c['cmd'][:80]} | {'ok' if not is_proof(c) else 'FAIL'} | {len(c['out'])}ch"
        f" | sha {h(c['out'])[:12]} | job:{job}:{c['line']}" for i, c in enumerate(calls))  # fmt: skip


def fetch(job: str, line: int) -> str | None:
    """Resolve a pointer: re-read the stored event and return its output text."""
    for n, raw in enumerate((JOBS / job / "output-1.jsonl").read_text(errors="replace").splitlines(), 1):
        if n == line:
            d = json.loads(raw)
            if d.get("type") == "tool_use":
                return str(d["part"].get("state", {}).get("output", ""))
            return str(d["item"].get("aggregated_output", ""))
    return None


def measure(job: str) -> dict:
    calls = calls_of(str(JOBS / job / "output-1.jsonl"))
    proofs = [(i, c) for i, c in enumerate(calls) if is_proof(c)]
    full, summ, state = render_full(calls), render_summary(calls), render_state(calls, job)
    rec_full = sum(1 for _, c in proofs if c["out"] in full)
    rec_summ = sum(1 for _, c in proofs if c["out"] in summ)
    rec_state, fetch_tokens = 0, 0
    for _, c in proofs:
        # the state names the call (FAIL flag + sha); the pointer is followed and the hash must match
        pointer = re.search(rf"sha {h(c['out'])[:12]} \| job:({re.escape(job)}):(\d+)", state)
        got = fetch(pointer.group(1), int(pointer.group(2))) if pointer else None
        if got is not None and h(got) == h(c["out"]):
            rec_state += 1
            fetch_tokens += tok(got)
    return {"job": job, "calls": len(calls), "proofs": len(proofs),
            "full": {"tokens": tok(full), "recovered": rec_full},
            "summary": {"tokens": tok(summ), "recovered": rec_summ},
            "state": {"tokens": tok(state), "recovered": rec_state, "fetch_tokens": fetch_tokens}}  # fmt: skip


def totals(rows: list[dict]) -> dict:
    n_proof = sum(r["proofs"] for r in rows)
    res = {"jobs": len(rows), "calls": sum(r["calls"] for r in rows), "proofs": n_proof}
    for arm in ("full", "summary", "state"):
        t = sum(r[arm]["tokens"] for r in rows)
        rec = sum(r[arm]["recovered"] for r in rows)
        extra = sum(r[arm].get("fetch_tokens", 0) for r in rows)
        res[arm] = {"tokens": t, "recovered": rec, "recall": round(rec / n_proof, 3) if n_proof else None,
                    "tokens_per_recovered_proof": round((t + extra) / rec) if rec else None,
                    "recovery_extra_tokens": extra}  # fmt: skip
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    files = sorted(glob.glob(str(JOBS / "*/output-1.jsonl")), key=os.path.getsize, reverse=True)[: a.jobs]
    rows = [measure(Path(f).parent.name) for f in files]
    rows = [r for r in rows if r["calls"] >= 20]
    result = {"rows": rows, "totals": totals(rows)}
    print(json.dumps(result, indent=1) if a.json else json.dumps(result["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
