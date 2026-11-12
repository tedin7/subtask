"""A built-in engine is built on its own, as the same object the full registry lists."""

import subprocess
import sys

from subtask.engines import registry


def test_builtin_names_keep_their_order():
    assert list(registry._builtins()) == [
        "sonnet-api", "haiku-api", "opus-api", "sonnet-api2", "haiku-api2", "opus-api2",
        "codex", "openai", "kimi", "opencode-go", "opencode", "dsh",
    ]  # fmt: skip


def test_get_builtin_is_the_listed_instance():
    registry.reset()
    codex = registry.get("codex")
    assert registry.all_engines()["codex"] is codex
    assert all(registry.get(name) is adapter for name, adapter in registry.all_engines().items())
    assert registry.get("no-such-engine") is None


def test_get_one_builtin_does_not_import_the_others():
    code = (
        "import sys; from subtask.engines import registry; registry.get('codex'); "
        "print(sorted(m for m in sys.modules if m.startswith('subtask.engines.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "subtask.engines.codex" in out
    assert "subtask.engines.kimi" not in out
