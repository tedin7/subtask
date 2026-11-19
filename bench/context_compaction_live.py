"""T-1002921 (live part): does a model recover the exact proof from each way of carrying a session?

Offline half: bench/context_compaction.py. This half spends money: for each participant session
and each target proof it asks one model the SAME question under three arms (full history, summary,
state with pointers) and checks the answer against the stored output.

Question: "call #I failed (command shown); quote the last non-empty line of its exact output."
Success: the answer contains that line verbatim (whitespace-normalised). The state arm may read the
stored session with a shell (pointer FILE:LINE); full and summary arms have no tools.
Cost per successful result = total cost of all trials of the arm / number of successes.

Run: python bench/context_compaction_live.py --out DIR [--sessions 12] [--targets 2] [--model M] [--max-usd 12]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import context_compaction as cc  # noqa: E402

ARMS = ("full", "summary", "state")
MIN_CALLS, MIN_TOK, MAX_TOK = 150, 50_000, 140_000


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def last_line(out: str) -> str:
    lines = [norm(x) for x in out.splitlines() if norm(x)]
    return lines[-1] if lines else ""


def pick_participants(n: int, targets: int) -> list[dict]:
    """Deterministic: sessions of 150+ calls and 50k-140k tokens, with failing calls well inside."""
    rows = []
    for f in sorted(cc.JOBS.glob("*/output-1.jsonl")):
        if f.stat().st_size > 2_000_000 or f.stat().st_size < 150_000:
            continue
        calls = cc.calls_of(str(f))
        if len(calls) < MIN_CALLS:
            continue
        t = cc.tok(cc.render_full(calls))
        if not MIN_TOK <= t <= MAX_TOK:
            continue
        k = cc.KEEP + 12
        mids = [i for i, c in enumerate(calls)
                if cc.is_proof(c) and k <= i < len(calls) - k and 12 <= len(last_line(c["out"])) <= 200]  # fmt: skip
        # the answer line must be unique in the session so a lucky guess cannot match another call
        mids = [i for i in mids if sum(last_line(c["out"]) == last_line(calls[i]["out"]) for c in calls) == 1]
        if mids:
            rows.append({"job": f.parent.name, "calls": len(calls), "tokens": t, "mids": mids})
    rows.sort(key=lambda r: (-r["calls"], r["job"]))
    for r in rows:
        r["mids"] = r["mids"][:: max(1, len(r["mids"]) // targets)][:targets]
    return rows[:n]


def context_for(arm: str, calls: list[dict], job: str, local: str) -> str:
    if arm == "full":
        return cc.render_full(calls)
    if arm == "summary":
        return cc.render_summary(calls)
    state = cc.render_state(calls, job)
    return state.replace(f"job:{job}:", f"{local}:")


def prompt_for(arm: str, ctx: str, idx: int, cmd: str) -> str:
    head = ("You receive the record of a past work session (one block per tool call, numbered #N). "
            "Task: call #%d failed. Its command was:\n%s\nReply with the last non-empty line of its exact "
            "output, copied verbatim, on a line starting with ANSWER: . If you cannot find it, reply "
            "ANSWER: UNKNOWN. Never invent text.\n" % (idx, cmd[:200]))
    if arm == "state":
        head += ("Each line of the record ends with a pointer FILE:LINE: FILE is a JSONL file in the current "
                 "directory and LINE its line number holding the full stored call. Read only what you need "
                 "with a shell (for example sed -n 'LINEp' FILE); do not read anything else.\n")
    else:
        head += "Use no tools and read no files.\n"
    return head + "\n\n"


def run_model(prompt: str, ctx: str, model: str, cwd: Path, tools: bool, timeout: int) -> dict:
    """One question to Claude through the same SDK and key helper that subtask's claude-api engine uses."""
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    from subtask.engines import claude_api

    async def go() -> dict:
        options = ClaudeAgentOptions(
            cwd=str(cwd), model=model, max_turns=12 if tools else 1,
            permission_mode="dontAsk", allowed_tools=["Read", "Grep"] if tools else [],
            disallowed_tools=["mcp__*", "WebFetch", "WebSearch", "Bash", "Edit", "Write"] + ([] if tools else ["Read", "Grep", "Glob"]),
            settings=claude_api.settings(False, None), system_prompt="You answer precisely and briefly.",
            env=claude_api.api_env(),
        )  # fmt: skip
        text, res = "", {"cost": 0.0, "tokens": {}, "tool_calls": 0, "rc": 1}
        async for m in query(prompt=prompt + "\n\nRECORD:\n" + ctx, options=options):
            if isinstance(m, ResultMessage):
                text = m.result or ""
                u = {k: v for k, v in (m.usage or {}).items() if isinstance(v, int)}
                res = {"cost": float(m.total_cost_usd or 0.0), "tokens": u, "tool_calls": max(m.num_turns - 1, 0),
                       "rc": 1 if m.is_error else 0}  # fmt: skip
        return {"text": text, **res}

    try:
        return asyncio.run(asyncio.wait_for(go(), timeout))
    except Exception as exc:  # a failed trial is a failed trial with its cost unknown, never a hidden skip
        return {"text": "", "cost": 0.0, "tokens": {"error": str(exc)[:200]}, "tool_calls": 0, "rc": -1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", type=int, default=12)
    ap.add_argument("--targets", type=int, default=2)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-usd", type=float, default=12.0)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    parts = pick_participants(a.sessions, a.targets)
    if a.list:
        print(json.dumps(parts, indent=1))
        return 0
    results = out / "trials.jsonl"
    done = {(r["job"], r["idx"], r["arm"]) for r in map(json.loads, results.read_text().splitlines())} if results.exists() else set()  # fmt: skip
    spent = sum(json.loads(x)["cost"] for x in results.read_text().splitlines()) if results.exists() else 0.0
    for p in parts:
        calls = cc.calls_of(str(cc.JOBS / p["job"] / "output-1.jsonl"))
        work = out / p["job"]
        work.mkdir(exist_ok=True)
        local = f"{p['job']}.jsonl"
        shutil.copy(cc.JOBS / p["job"] / "output-1.jsonl", work / local)
        for idx in p["mids"]:
            for arm in ARMS:
                if (p["job"], idx, arm) in done:
                    continue
                if spent >= a.max_usd:
                    print("budget reached", spent)
                    return 0
                ctx = context_for(arm, calls, p["job"], local)
                pr = prompt_for(arm, ctx, idx, calls[idx]["cmd"])
                r = run_model(pr, ctx, a.model, work, arm == "state", a.timeout)
                want = last_line(calls[idx]["out"])
                m = re.findall(r"ANSWER:\s*(.*)", r["text"])
                got = norm(m[-1]) if m else ""
                ok = bool(got) and got != "UNKNOWN" and want in norm(got) or (bool(got) and norm(got) in want and len(got) > 20)  # fmt: skip
                row = {"job": p["job"], "idx": idx, "arm": arm, "ok": bool(ok), "want": want, "got": got[:300],
                       "prompt_tokens_est": cc.tok(pr + ctx), **{k: r[k] for k in ("cost", "tokens", "tool_calls", "rc")}}  # fmt: skip
                spent += r["cost"]
                with results.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(p["job"], idx, arm, row["ok"], round(r["cost"], 4), r["tokens"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
