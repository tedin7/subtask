"""Run a bounded live Jev -> normal cascade; never replay it as a live trial."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from subtask.jev_filter import route

from . import jev_phase_eval as phase


def run(root: Path, slot: int, reservations: Path, *, execute: bool = False) -> dict:
    """The gold labels remain outside inference; baseline calls are explicit."""
    root = phase._root(root)
    manifest = phase._read_json(root / "manifest.json")
    entry = manifest["slots"][str(slot)]
    if entry.get("engines"):
        raise phase.PhaseError("slot already attempted; no live replay")
    required = ["C1"]
    if not execute:
        return {"execute": False, "slot": slot, "required_ids": required, "api_calls": 0}
    intent = root / "slots" / f"slot-{slot:02d}.cascade.intent.json"
    intent.parent.mkdir(parents=True, exist_ok=True)
    with intent.open("x") as stream:
        json.dump({"started": time.time(), "required_ids": required}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    start = time.monotonic()
    jev = phase.run_one(root, slot, "jev", execute=True, reservations=reservations)
    decision = route(jev, required)
    fallback = decision["route"] == "normal"
    if fallback:
        normal = phase.run_one(root, slot, "normal", execute=True, reservations=reservations)
        selected = normal
    else:
        selected = jev
    cascade_s = time.monotonic() - start
    # A decisive cascade finishes before this comparison-only normal call.
    if not fallback:
        normal = phase.run_one(root, slot, "normal", execute=True, reservations=reservations)
    result = {
        "slot": slot,
        "case": entry["case"],
        "phase": entry["phase"],
        "advisory_only": True,
        "scope": "C1 only; no acceptance or full-request certification",
        "route": decision,
        "normal_called_by_cascade": fallback,
        "normal_called_for_comparison_only": not fallback,
        "selected_status": selected.get("status"),
        "selected_answers": selected.get("answers"),
        "cascade_wall_s": cascade_s,
        "normal_component_s": normal.get("latency_s"),
        "jev": jev,
        "normal": normal,
        "comparison_limit": "normal baseline reused on fallback; fixed Jev-first order; CLI startup included",
    }
    phase._write_json(root / "slots" / f"slot-{slot:02d}.cascade.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--reservations", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.slot, args.reservations, execute=args.execute)))


if __name__ == "__main__":
    main()
