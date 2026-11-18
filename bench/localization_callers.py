"""T-1002924: locating the code to fix by words versus by callers, on a fixed set of real fixes.

Offline and free (no model, cost 0 USD; model and resources are not involved, so they are unchanged).
Problems: the first N `fix:` commits (newest first, from a fixed commit) of this repository that change
1 to 6 files under subtask/ (tests and docs ignored). For each one the repository is read AT THE PARENT
of the fix (blobs through git, no checkout) and the problem text is the commit subject.

  words     rank files by weighted keyword hits of the problem text
  callers   take the two best word hits as seeds, then add the files that call a symbol the seeds
            define (callers) or define a symbol the seeds call (callees), ranked by how many seeds
            they touch plus their own word score

Measured against the files the real fix changed: precision@k, recall@k and omissions (changed files not
in the top k: the related edits a fixer working from that list would miss). N is small: it is a
comparison on a fixed sample, not a proof about every bug.

Run: python bench/localization_callers.py [--n 20] [--k 3] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import subprocess
from collections import Counter, defaultdict

BASE = "origin/main"
STOP = {
    "with",
    "that",
    "this",
    "from",
    "when",
    "into",
    "then",
    "than",
    "the",
    "and",
    "for",
    "not",
    "fix",
    "now",
    "one",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def fixes(n: int) -> list[dict]:
    out = []
    for line in git("log", BASE, "--format=%H\t%P\t%s", "-300").splitlines():
        sha, parents, subject = line.split("\t", 2)
        if not subject.startswith("fix") or not parents.strip():
            continue
        files = [f for f in git("diff", "--name-only", f"{sha}^", sha).splitlines()
                 if f.startswith("subtask/") and f.endswith(".py")]  # fmt: skip
        if 1 <= len(files) <= 6:
            out.append({"sha": sha, "parent": parents.split()[0], "subject": subject, "files": files})
        if len(out) == n:
            break
    return out


def index(parent: str) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for f in git("ls-tree", "-r", "--name-only", parent, "subtask").splitlines():
        if not f.endswith(".py"):
            continue
        text = git("show", f"{parent}:{f}")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        defs = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        calls = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                 for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}  # fmt: skip
        idx[f] = {"text": text.lower(), "defs": defs, "calls": calls}
    return idx


def words(subject: str) -> list[str]:
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", re.sub(r"\(.*?\)|^fix[:(]?\s*", "", subject))
    return [t.lower() for t in toks if t.lower() not in STOP]


def word_scores(idx: dict[str, dict], toks: list[str]) -> dict[str, float]:
    df = Counter(t for t in toks for d in idx.values() if t in d["text"])
    sc: dict[str, float] = defaultdict(float)
    for f, d in idx.items():
        for t in toks:
            hits = d["text"].count(t)
            if hits:
                sc[f] += math.log1p(hits) * math.log(1 + len(idx) / (1 + df[t]))
    return sc


def by_words(idx: dict[str, dict], toks: list[str], k: int) -> list[str]:
    sc = word_scores(idx, toks)
    return sorted(sc, key=lambda f: -sc[f])[:k]


def by_callers(idx: dict[str, dict], toks: list[str], k: int) -> list[str]:
    sc = word_scores(idx, toks)
    seeds = sorted(sc, key=lambda f: -sc[f])[:2]
    link: dict[str, float] = defaultdict(float)
    for s in seeds:
        for f, d in idx.items():
            if f == s:
                continue
            if d["calls"] & (idx[s]["defs"] - {"__init__", "main"}):
                link[f] += 1.0  # f calls something the seed defines: a caller
            if idx[s]["calls"] & (d["defs"] - {"__init__", "main"}):
                link[f] += 0.5  # the seed calls something f defines: a callee
    rank = {f: (3.0 if f in seeds else 0.0) + link[f] + 0.2 * sc.get(f, 0) for f in set(link) | set(seeds)}
    return sorted(rank, key=lambda f: -rank[f])[:k]


def score(pred: list[str], truth: list[str]) -> dict:
    hit = len(set(pred) & set(truth))
    return {"precision": hit / len(pred) if pred else 0.0, "recall": hit / len(truth),
            "omitted": len(set(truth) - set(pred)), "all_found": set(truth) <= set(pred)}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = []
    for fx in fixes(a.n):
        idx = index(fx["parent"])
        toks = words(fx["subject"])
        truth = [f for f in fx["files"] if f in idx]
        if not truth or not toks:
            continue
        rows.append({"sha": fx["sha"][:9], "subject": fx["subject"][:70], "truth": truth,
                     "words": score(by_words(idx, toks, a.k), truth),
                     "callers": score(by_callers(idx, toks, a.k), truth)})  # fmt: skip
    summary = {"k": a.k, "problems": len(rows)}
    for arm in ("words", "callers"):
        summary[arm] = {
            "precision": round(sum(r[arm]["precision"] for r in rows) / len(rows), 3),
            "recall": round(sum(r[arm]["recall"] for r in rows) / len(rows), 3),
            "omitted_files": sum(r[arm]["omitted"] for r in rows),
            "problems_fully_covered": sum(r[arm]["all_found"] for r in rows),
        }
    print(json.dumps({"rows": rows, "summary": summary} if a.json else summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
