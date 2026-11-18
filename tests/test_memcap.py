import subprocess

import pytest

from subtask import memcap


@pytest.fixture
def systemd(monkeypatch):
    monkeypatch.setattr(memcap, "_on_systemd", lambda: True)
    monkeypatch.setattr(memcap, "_slice_exists", lambda name: True)


def test_job_runs_in_a_scope_with_memory_high_and_max(systemd):
    out = memcap.cap_argv(["python", "w"], {})
    assert out[:2] == ["systemd-run", "--scope"]
    assert "-p" in out and "MemoryHigh=1536M" in out and "MemoryMax=3G" in out
    assert "--slice=arvos-agenti.slice" in out
    assert out[-2:] == ["python", "w"]


def test_env_changes_the_cap(systemd):
    out = memcap.cap_argv(["x"], {"SUBTASK_JOB_MEMORY_HIGH": "2G", "SUBTASK_JOB_MEMORY_MAX": "off"})
    assert "MemoryHigh=2G" in out
    assert not any(a.startswith("MemoryMax") for a in out)


@pytest.mark.parametrize("env", [{"SUBTASK_JOB_MEMORY_HIGH": "off"}, {"SUBTASK_JOB_SLICE": "0"}])
def test_switched_off_leaves_argv_unchanged(systemd, env):
    assert memcap.cap_argv(["x"], env) == ["x"]


def test_without_systemd_or_slice_argv_is_unchanged(monkeypatch):
    monkeypatch.setattr(memcap, "_on_systemd", lambda: False)
    assert memcap.cap_argv(["x"], {}) == ["x"]
    monkeypatch.setattr(memcap, "_on_systemd", lambda: True)
    monkeypatch.setattr(memcap, "_slice_exists", lambda name: False)
    assert memcap.cap_argv(["x"], {}) == ["x"]


def test_real_scope_applies_memory_high_on_this_host():
    if not memcap._on_systemd() or not memcap._slice_exists(memcap.DEFAULT_SLICE):
        pytest.skip("no systemd slice here")
    argv = memcap.cap_argv(["sh", "-c", "cat /proc/self/cgroup"], {})
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    assert "arvos-agenti.slice" in out
