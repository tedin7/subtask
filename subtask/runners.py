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
    "kimi": [".kimi-code", ".kimi", ".config/kimi", ".local/share/kimi", ".cache/kimi"],
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
    """bwrap installed AND able to create its namespaces here (some hosts forbid user namespaces)."""
    exe = shutil.which("bwrap")
    if not exe:
        return False
    import subprocess

    try:
        done = subprocess.run(
            [exe, "--ro-bind", "/", "/", "--unshare-pid", "--", "true"],
            capture_output=True, timeout=10, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def direct(argv: list[str], env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    return argv, env


def isolated(spec: dict, family: str):
    """A wrap() for RunContext that runs argv under bwrap, or raises if bwrap is missing."""
    bwrap = shutil.which("bwrap")
    if not bwrap or not bwrap_available():
        raise RuntimeError("policy isolated needs a working bubblewrap (bwrap); refusing to run unisolated")
    home = Path.home()

    def wrap(argv: list[str], env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        args = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]
        for path in SYSTEM_RO:
            if os.path.exists(path):
                args += ["--ro-bind", path, path]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", str(home)]
        exe = shutil.which(argv[0], path=env.get("PATH"))
        if exe:
            for candidate in {Path(exe).parent, Path(exe).resolve().parent}:
                if not any(str(candidate).startswith(p + "/") or str(candidate) == p for p in SYSTEM_RO):
                    # the engine binary lives outside the system dirs (e.g. ~/.local/bin): read-only
                    args += ["--ro-bind-try", str(candidate), str(candidate)]
            real = Path(exe).resolve()
            if home in real.parents:
                parts = real.relative_to(home).parts
                depth = 3 if parts[0] == ".local" else 1  # ~/.local/share/<tool>, or ~/.<tool>
                if len(parts) > depth:
                    root = home.joinpath(*parts[:depth])
                    args += ["--ro-bind-try", str(root), str(root)]
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
