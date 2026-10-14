"""R12, R13: policy matrix, a working isolated runner, minimal env, general redaction.

Secrets here are synthetic. Nothing reads real credentials.
"""

from __future__ import annotations

import subprocess

import pytest

from .conftest import fake_cli, wait_final


def test_policy_matrix_is_enforced_not_scored(home):
    from subtask import policy
    from subtask.engines import registry

    reg = registry.all_engines()
    ro = policy.demand(readonly=True)
    assert policy.effective(reg["kimi"], ro)[0] is None
    assert policy.effective(reg["opencode-go"], ro)[0] is None
    assert policy.effective(reg["codex"], ro)[0] == "readonly"
    default = policy.demand()
    assert policy.effective(reg["codex"], default)[0] == "sandbox"  # strongest native, never the bypass
    assert policy.effective(reg["sonnet-api"], default)[0] == "sandbox"
    assert policy.effective(reg["kimi"], default) == ("worktree", "git worktree only, not a host sandbox")
    iso = policy.demand(level="isolated")
    assert policy.effective(reg["kimi"], iso)[0] == "isolated"
    assert policy.effective(reg["sonnet-api"], iso)[0] is None  # no silent downgrade
    untrusted = policy.demand(level="untrusted")
    assert policy.effective(reg["kimi"], untrusted)[0] is None
    assert policy.effective(reg["codex"], untrusted)[0] == "sandbox"
    with pytest.raises(ValueError):
        policy.demand(level="yolo")


def test_router_never_trades_security_for_cost(home):
    from subtask import policy, router

    engine, _reason, options = router.pick("code", None, policy.demand(level="isolated"))
    for o in options:
        if o.engine in ("sonnet-api", "opus-api", "haiku-api", "dsh"):
            assert not o.admissible
    assert engine in ("", "codex", "kimi", "opencode-go")


def test_start_refuses_what_it_cannot_enforce(home):
    from subtask import core

    out = core.start("x", engine="sonnet-api", cwd=str(home), policy="isolated")
    assert "cannot run isolated" in out
    assert "cannot run read-only" in core.start("x", engine="opencode-go", cwd=str(home), readonly=True)


def _bwrap_works() -> bool:
    from subtask.runners import bwrap_available

    return bwrap_available()


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap missing or user namespaces blocked on this host")
def test_isolated_runner_hides_home_and_blocks_writes_outside(home, tmp_path, monkeypatch):
    from subtask import core

    fake_home = tmp_path / "realhome"
    (fake_home / ".ssh").mkdir(parents=True)
    (fake_home / ".ssh" / "id_test").write_text("SYNTHETIC-SECRET-123")
    monkeypatch.setenv("HOME", str(fake_home))
    outside = tmp_path / "outside.txt"
    work = tmp_path / "work"
    work.mkdir()
    script = (
        f"cat {fake_home}/.ssh/id_test 2>/dev/null && echo LEAK; "
        f"echo x > {outside} 2>/dev/null && echo WROTE-OUTSIDE; "
        f"echo ok > {work}/inside.txt && echo WROTE-INSIDE"
    )
    fake_cli(home, "kimi", script)
    out = core.start("x", engine="kimi", cwd=str(work), policy="isolated")
    assert "isolated" in out, out
    final = wait_final(out.split()[0])
    from subtask import jobs

    text = (jobs.job_dir(out.split()[0]) / "result.md").read_text()
    assert "LEAK" not in text and "SYNTHETIC" not in text
    assert not outside.exists()  # writes outside the worktree land in a throwaway tmpfs
    assert "WROTE-ETC" not in text
    assert "WROTE-INSIDE" in text and (work / "inside.txt").is_file()
    assert final["state"] == "done"


def test_worker_env_is_an_allow_list(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SOME_RANDOM_VAR", "visible-to-parent")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 36)
    fake_cli(home, "kimi", 'env | grep -c -E "SOME_RANDOM_VAR|GITHUB_TOKEN|ANTHROPIC" ; exit 0')
    job = core.start("count", engine="kimi", cwd=str(home)).split()[0]
    wait_final(job)
    assert (jobs.job_dir(job) / "result.md").read_text().strip() == "0"


SYNTHETIC = {
    "anthropic": "sk-ant-api03-" + "A" * 40,
    "openai": "sk-proj-" + "b" * 40,
    "github": "ghp_" + "c" * 36,
    "github-fine": "github_pat_" + "d" * 40,
    "aws": "AKIA" + "E" * 16,
    "google": "AIza" + "f" * 35,
    "slack": "xoxb-" + "1" * 20,
    "stripe": "sk_live_" + "g" * 24,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    "url": "https://user:hunter2pass@example.com/x",
    "assign": "DB_PASSWORD=correcthorsebattery",
}


@pytest.mark.parametrize("kind", list(SYNTHETIC))
def test_redaction_masks_common_secret_shapes(kind):
    from subtask.redact import redact

    secret = SYNTHETIC[kind]
    out = redact(f"before {secret} after")
    core = secret.split("=")[-1].split("@")[0].split(":")[-1]
    assert core not in out, (kind, out)
    assert "before" in out and "after" in out


def test_redaction_keeps_ordinary_text():
    from subtask.redact import redact

    text = "commit 3f731f60ebcacba35c0d92f6cc4b917b836517ce, tests passed: 34, sk-learn is a library"
    assert redact(text) == text


def test_known_secret_values_are_masked_whatever_their_shape():
    from subtask import redact

    redact.remember(["zz-custom-secret-value-42"])
    assert "zz-custom" not in redact.redact("value zz-custom-secret-value-42 here")


def test_results_events_and_tool_output_are_redacted(home):
    from subtask import core, events, jobs

    fake_cli(home, "kimi", f'echo "found {SYNTHETIC["github"]}"')
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    wait_final(job)
    assert SYNTHETIC["github"] not in (jobs.job_dir(job) / "result.md").read_text()
    assert SYNTHETIC["github"] not in core.result(job, full=True)
    events.emit(job, "tool", f"leak {SYNTHETIC['aws']}")
    assert SYNTHETIC["aws"] not in (jobs.job_dir(job) / "progress.jsonl").read_text()


def test_cleanup_never_removes_ignored_or_dirty_work(home, tmp_path):
    from subtask import core, jobs

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("build/\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        check=True,
    )
    fake_cli(home, "kimi", "mkdir -p build && echo x > build/out.bin; echo done")
    job = core.start("x", engine="kimi", repo=str(repo)).split()[0]
    wait_final(job)
    assert "not removed" in core.cleanup(job)
    from pathlib import Path

    assert (Path(jobs.spec(job)["cwd"]) / "build" / "out.bin").is_file()


def test_isolated_is_refused_when_bwrap_cannot_run(home, monkeypatch):
    from subtask import core, runners

    monkeypatch.setattr(runners, "bwrap_available", lambda: False)
    out = core.start("x", engine="kimi", cwd=str(home), policy="isolated")
    assert "bubblewrap" in out and "started" not in out


def test_progress_log_survives_multibyte_text_across_the_tail_boundary(home):
    """Reading the last event seeks back 4096 bytes: that may land inside a UTF-8 character."""
    import json

    from subtask import events, jobs

    folder = jobs.job_dir("st-utf8utf800")
    folder.mkdir(parents=True)
    line = json.dumps({"seq": 1, "msg": "…" * 50}, ensure_ascii=False) + "\n"  # "…" is 3 bytes
    for pad in range(3):  # one of the three alignments puts byte -4096 inside a "…"
        body = ("x" * pad + "\n" + line * 40).encode()
        (folder / "progress.jsonl").write_bytes(body)
        assert events.emit("st-utf8utf800", "tool", "dopo", source="adapter") is not None, pad
        assert events.last("st-utf8utf800")["msg"] == "dopo"
