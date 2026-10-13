"""Settings, all overridable from the environment. No personal data lives here."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.environ.get("SUBTASK_HOME", Path.home() / ".local" / "share" / "subtask"))
JOBS = HOME / "jobs"
WORKTREES = HOME / "worktrees"
LEDGER = HOME / "ledger.jsonl"

# KEY=VALUE file holding ANTHROPIC_API_KEY (and optionally ANTHROPIC_WORKSPACE_ID).
# Keep it chmod 600; it is read by the worker only, never returned by a tool.
ENV_FILE = Path(os.environ.get("SUBTASK_ENV_FILE", Path.home() / ".config" / "subtask" / "env"))

MONTHLY_CAP_USD = float(os.environ.get("SUBTASK_MONTHLY_CAP_USD", "180"))
DEFAULT_MAX_USD = float(os.environ.get("SUBTASK_DEFAULT_MAX_USD", "2"))
MAX_RUNNING = int(os.environ.get("SUBTASK_MAX_RUNNING", "4"))
SUMMARY_LINES = 12


def load_secrets() -> dict[str, str]:
    """Read the env file; values already in the process environment win."""
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values
