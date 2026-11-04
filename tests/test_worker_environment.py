import signal
import sys
from types import SimpleNamespace

from subtask import jobs, worker


def test_worker_does_not_forward_daemon_codex_home(home, monkeypatch):
    job = "st-envtest000"
    folder = jobs.job_dir(job)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "x", "engine": "new", "cwd": str(home)})
    jobs.write_json(folder / "status.json", {"state": "queued"})
    monkeypatch.setenv("CODEX_HOME", "/daemon/codex")
    monkeypatch.setenv("CODEX_HOME_EXTRA", "also-private")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    monkeypatch.setenv("KIMI_CODE_HOME", "/kimi")
    monkeypatch.setattr(sys, "argv", ["worker", job])
    captured = {}

    def run(ctx):
        captured.update(ctx.env)
        raise RuntimeError("end test")

    adapter = SimpleNamespace(family="new", capabilities=SimpleNamespace(paid=False), run=run)
    monkeypatch.setattr(worker.registry, "get", lambda _: adapter)
    old_handler = signal.getsignal(signal.SIGTERM)
    try:
        worker.main()
    finally:
        signal.signal(signal.SIGTERM, old_handler)

    assert "CODEX_HOME" not in captured
    assert "CODEX_HOME_EXTRA" not in captured
    assert captured["XDG_CONFIG_HOME"] == "/xdg"
    assert captured["KIMI_CODE_HOME"] == "/kimi"
    assert captured["SUBTASK_ID"] == job
