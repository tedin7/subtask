"""T-1000833: a plain start_many past the slot cap queues the rest in a group instead of losing it."""

from __future__ import annotations

SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def test_plain_batch_over_cap_queues_rest(home, monkeypatch):
    from subtask import core, groups

    monkeypatch.setenv("SUBTASK_GROUP_TICK_S", "0.2")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 1; " + SAY)
    base = {"engine": "fake", "cwd": str(home), "max_usd": 0.5}
    out = core.start_many([{**base, "task": f"t{i}"} for i in range(6)])
    assert out.count(" started ") >= 2
    assert "already running: use wait()" not in out, out
    assert "more queued" in out, out
    gid = next(w for w in out.split() if w.startswith("grp-"))
    final = core.wait([gid], timeout=90)
    assert not final.startswith("still running"), final
    rows = groups.read(gid)["tasks"]
    assert rows and all(r["state"] == "started" and r["job"] for r in rows), rows
    assert len(out.split("started")) - 1 + len(rows) == 6, out
