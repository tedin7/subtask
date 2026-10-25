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
CURSOR_WAIT_MAX_S = 60
WAIT_MAX_S = 600
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


def wait_budget(timeout: float, since: str | None, hard_max: float = WAIT_MAX_S) -> float:
    """Seconds a blocking wait may run before it returns "still running".

    A wait with a cursor is the delivery path for clients that do not render
    progress and cannot be woken by an MCP notification (Codex 0.164.0 is one:
    see docs/progress-matrix.md). Keep that path inside 60 s so it also fits
    Codex's 300 s default MCP tool timeout, not only the 660 s the installer
    writes. A client that shows progress may still block for the full 600 s.
    """
    try:
        requested = float(timeout)
    except (TypeError, ValueError):
        requested = 1.0
    limit = CURSOR_WAIT_MAX_S if since is not None else hard_max
    return max(1.0, min(requested, float(limit)))


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
        self.changed: dict[str, float] = {}  # monotonic time of each job's last real change
        self.detailed: dict[str, bool] = {}  # True once the engine reported its own activity
        self.sent_ok = 0
        self.send_errors = 0
        for job_id in ids or []:
            self.add(job_id)

    def add(self, job_id: str) -> None:
        if job_id not in self.offsets:
            self.ids.append(job_id)
            # Start from the current end: only what happens during this call is news.
            _, self.offsets[job_id] = events.read_since(job_id, 0, limit=10**9)
            last = events.last(job_id)
            self.activity[job_id] = _describe(last)
            self.detailed[job_id] = last.get("source") == "adapter"
            self.changed[job_id] = self.now()

    def phase(self, text: str, force: bool = False) -> None:
        """A step of this call itself (admission, worktree...), not of a job."""
        self._maybe_send(text, significant=True, force=force)

    def poll(self) -> None:
        significant = False
        for job_id in self.ids:
            new, self.offsets[job_id] = events.read_since(job_id, self.offsets[job_id])
            for event in new:
                text = _describe(event)
                if event.get("source") == "adapter":
                    self.detailed[job_id] = True
                if text and text != self.activity.get(job_id):
                    self.activity[job_id] = text
                    self.changed[job_id] = self.now()
                    significant = True
            state = jobs.status(job_id).get("state", "unknown")
            if state != self.states.get(job_id):
                self.states[job_id] = state
                self.changed[job_id] = self.now()
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
            if state in jobs.FINAL:
                return f"{job} {state} — {self.activity.get(job) or state}"
            return f"{job} — {self._what(job)}"
        head = f"{len(ended)}/{len(self.ids)} ended"
        if running:
            job = max(running, key=lambda j: self.changed.get(j, 0.0))  # the latest real change
            return f"{head} — {job} {self._what(job)}"
        failed = sum(1 for j in ended if self.states.get(j) != "done")
        return f"{head} — {len(ended) - failed} done, {failed} not done"

    def _what(self, job: str) -> str:
        """The job's latest activity; says so plainly when the engine exposes none."""
        quiet = self.now() - self.changed.get(job, self.t0) > 5
        if self.states.get(job) == "running" and not self.detailed.get(job) and quiet:
            return NO_DETAIL
        return self.activity.get(job) or NO_DETAIL

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


def _describe(event: dict) -> str:
    if not event:
        return ""
    phase, msg = str(event.get("phase") or ""), str(event.get("msg") or "")
    if phase == "tool":
        return msg
    label = LABELS.get(phase, phase) or ""
    return f"{label}: {msg}" if msg and label else msg or label
