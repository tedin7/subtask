"""Turn job events into short progress lines while a tool call is running.

`Reporter` is protocol-agnostic: it gets a `send(progress, message, total)`
callable from the server. It never invents a percentage: `progress` is a
counter of updates (MCP requires it to grow), the jobs-finished ratio is in
the message, and elapsed time is wall time of this call (monotonic clock).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from . import events, jobs

MIN_INTERVAL_S = float(os.environ.get("SUBTASK_PROGRESS_MIN_S", "2"))
HEARTBEAT_S = float(os.environ.get("SUBTASK_PROGRESS_HEARTBEAT_S", "10"))
NO_DETAIL = "worker running; internal activity not available"
LABELS = {
    "admission": "admitting",
    "worktree": "preparing worktree",
    "spawn": "starting worker",
    "start": "worker started",
    "tool": "",
    "verify": "verifying",
    "reply": "resuming",
}


def clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return (
        f"{seconds // 60:02d}:{seconds % 60:02d}"
        if seconds < 3600
        else f"{seconds // 3600}h{seconds % 3600 // 60:02d}"
    )


class Reporter:
    """Coalesces events of the given jobs into throttled notifications."""

    def __init__(
        self,
        send: Callable[[float, str, float | None], None],
        ids: list[str] | None = None,
        label: str = "Subtask",
        now: Callable[[], float] = time.monotonic,
        min_interval: float = MIN_INTERVAL_S,
        heartbeat: float = HEARTBEAT_S,
    ) -> None:
        self.send, self.label, self.now = send, label, now
        self.min_interval, self.heartbeat = min_interval, heartbeat
        self.t0 = now()
        self.count = 0
        self.last_sent = -1e9
        self.last_text = ""
        self.ids: list[str] = []
        self.offsets: dict[str, int] = {}
        self.activity: dict[str, str] = {}
        self.states: dict[str, str] = {}
        self.sent_ok = 0
        self.send_errors = 0
        for job_id in ids or []:
            self.add(job_id)

    def add(self, job_id: str) -> None:
        if job_id not in self.offsets:
            self.ids.append(job_id)
            # Start from the current end: only what happens during this call is news.
            _, self.offsets[job_id] = events.read_since(job_id, 0, limit=10**9)
            self.activity[job_id] = _describe(events.last(job_id))

    def phase(self, text: str, force: bool = False) -> None:
        """A step of this call itself (admission, worktree...), not of a job."""
        self._maybe_send(text, significant=True, force=force)

    def poll(self) -> None:
        significant = False
        for job_id in self.ids:
            new, self.offsets[job_id] = events.read_since(job_id, self.offsets[job_id])
            for event in new:
                text = _describe(event)
                if text and text != self.activity.get(job_id):
                    self.activity[job_id] = text
                    significant = True
            state = jobs.status(job_id).get("state", "unknown")
            if state != self.states.get(job_id):
                self.states[job_id] = state
                significant = True
        self._maybe_send(self.summary(), significant)

    def summary(self) -> str:
        if not self.ids:
            return ""
        ended = [j for j in self.ids if self.states.get(j) in jobs.FINAL]
        running = [j for j in self.ids if j not in ended]
        if len(self.ids) == 1:
            job = self.ids[0]
            state = self.states.get(job, "?")
            what = self.activity.get(job) or (NO_DETAIL if state == "running" else state)
            return f"{job} · {state} — {what}" if state in jobs.FINAL else f"{job} — {what}"
        head = f"{len(ended)}/{len(self.ids)} ended"
        if running:
            job = running[-1] if len(running) == 1 else _busiest(running, self.activity)
            what = self.activity.get(job) or NO_DETAIL
            return f"{head} — {job} {what}"
        failed = sum(1 for j in ended if self.states.get(j) != "done")
        return f"{head} — {len(ended) - failed} done, {failed} not done"

    def _maybe_send(self, text: str, significant: bool, force: bool = False) -> None:
        if not text:
            return
        t = self.now()
        due_change = significant and text != self.last_text and t - self.last_sent >= self.min_interval
        due_beat = t - self.last_sent >= self.heartbeat
        if not (force or due_change or due_beat):
            return
        self.count += 1
        self.last_sent, self.last_text = t, text
        message = f"{self.label} · {clock(t - self.t0)} · {text}"
        try:
            self.send(float(self.count), message, None)
            self.sent_ok += 1
        except Exception:  # noqa: BLE001 - a broken client never stops the work
            self.send_errors += 1


def _busiest(ids: list[str], activity: dict[str, str]) -> str:
    with_detail = [j for j in ids if activity.get(j)]
    return with_detail[-1] if with_detail else ids[-1]


def _describe(event: dict) -> str:
    if not event:
        return ""
    phase, msg = str(event.get("phase") or ""), str(event.get("msg") or "")
    if phase == "tool":
        return msg
    label = LABELS.get(phase, phase) or ""
    return f"{label}: {msg}" if msg and label else msg or label
