"""How the engine process is launched: directly, or inside a bubblewrap sandbox.

The isolated runner is a real boundary for the filesystem: the engine sees a
read-only system, its worktree and git dir, and a private HOME that only holds
the engine's own state directories. It does not hide the engine's own
credentials from the engine (it needs them), and model traffic keeps the network.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SYSTEM_RO = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc", "/opt", "/nix", "/snap"]
BASE_ENV = ("PATH", "LANG", "LC_ALL", "TERM", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")

# Per engine family: state directories under HOME the engine needs (read-write).
STATE_DIRS = {
    "codex": [".codex"],
    "kimi": [".kimi", ".config/kimi", ".local/share/kimi", ".cache/kimi"],
    "opencode": [".local/share/opencode", ".config/opencode", ".cache/opencode", ".local/state/opencode"],
    "api": [".claude", ".claude.json"],
}


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in BASE_ENV if k in os.environ}
    env["HOME"] = str(Path.home())
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(extra or {})
    return env


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def direct(argv: list[str], env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    return argv, env


def isolated(spec: dict, family: str):
    """A wrap() for RunContext that runs argv under bwrap, or raises if bwrap is missing."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("policy isolated needs bubblewrap (bwrap) on PATH; refusing to run unisolated")
    home = Path.home()

    def wrap(argv: list[str], env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        args = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        for path in SYSTEM_RO:
            if os.path.exists(path):
                args += ["--ro-bind", path, path]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", str(home)]
        exe = shutil.which(argv[0], path=env.get("PATH"))
        if exe:
            real = Path(exe).resolve()
            for candidate in {Path(exe).parent, real.parent.parent if home in real.parents else real.parent}:
                if home in candidate.parents or candidate == home:
                    args += ["--ro-bind-try", str(candidate), str(candidate)]
        for rel in STATE_DIRS.get(family, []):
            target = home / rel
            if target.exists():
                args += ["--bind", str(target), str(target)]
        for key in ("cwd", "git_dir"):
            if spec.get(key):
                args += ["--bind", spec[key], spec[key]]
        args += ["--chdir", spec["cwd"], "--setenv", "HOME", str(home), "--"]
        return args + argv, env

    return wrap
