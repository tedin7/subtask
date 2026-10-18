"""`subtask install | uninstall | doctor` argument parsing and output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import claude_plugin
from .clients import CLIENTS, Client, InstallError
from .doctor import doctor
from .installer import install, uninstall

CHOICES = [*CLIENTS, "all"]


def add_parsers(sub: argparse._SubParsersAction) -> None:
    sk = sub.add_parser("install-skill", help="copy or update only SKILL.md for the agents")
    sk.add_argument("--dest", help="skills folder to write into (default: every installed client)")
    sk.add_argument("--client", default="all", choices=["all", *CLIENTS])
    i = sub.add_parser(
        "install", help="register the MCP server and skill in a client (plan only without --yes)"
    )
    u = sub.add_parser("uninstall", help="remove what `subtask install` added (plan only without --yes)")
    d = sub.add_parser("doctor", help="check a client: app, config entry, server handshake")
    for p in (i, u, d):
        p.add_argument(
            "--client", required=p is not d, choices=CHOICES, help="doctor without --client checks engines"
        )
        p.add_argument("--scope", default="user", choices=["user", "project"])
        p.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    for p in (i, u):
        p.add_argument(
            "--yes", action="store_true", help="apply the plan (default: print it, change nothing)"
        )
        p.add_argument(
            "--dry-run", action="store_true", help="print the plan only (the default without --yes)"
        )
    i.add_argument(
        "--force", action="store_true", help="replace an existing 'subtask' entry that is not ours"
    )
    for p in (i, u, d):
        p.add_argument("--no-plugin", action="store_true",
                       help="claude: register the server with `claude mcp add` instead of the plugin")  # fmt: skip
    i.add_argument("--command", help="server command to register (default: subtask-mcp next to this python)")


def server_command(override: str | None) -> list[str]:
    if override:
        return [override]
    here = Path(sys.executable).parent / "subtask-mcp"
    if here.is_file():
        return [str(here.absolute())]
    import shutil

    found = shutil.which("subtask-mcp")
    if not found:
        raise InstallError("subtask-mcp not found next to this python nor on PATH; pass --command PATH")
    return [str(Path(found).absolute())]


def _selected(name: str) -> tuple[list[Client], list[str]]:
    if name != "all":
        return [CLIENTS[name]()], []
    clients = [cls() for cls in CLIENTS.values()]
    return [c for c in clients if c.exe()], [c.name for c in clients if not c.exe()]


def run(a: argparse.Namespace) -> int:
    clients, missing = _selected(a.client)
    use_plugin = a.scope == "user" and not getattr(a, "no_plugin", False)
    if a.cmd == "doctor":
        reports = [
            claude_plugin.doctor(c)
            if use_plugin and c.name == "claude" and _plugin_mode(c)
            else doctor(c, a.scope)
            for c in clients
        ]
        ok = all(r.get("connects") for r in reports) and bool(reports)
        _emit(a.json, {"action": "doctor", "clients": reports, "not_detected": missing}, _doctor_text)
        return 0 if ok else 1
    apply = a.yes and not a.dry_run
    try:
        argv = server_command(a.command) if a.cmd == "install" else []
    except InstallError as exc:
        _emit(a.json, {"action": a.cmd, "error": str(exc)}, lambda d: f"error: {d['error']}")
        return 2
    results = []
    for c in clients:
        if a.cmd == "install" and not c.exe():
            results.append({"client": c.name, "scope": a.scope, "status": "skipped",
                            "steps": [], "notes": [f"{c.binary} not found on PATH; install the app first"]})  # fmt: skip
            continue
        if c.name == "claude" and use_plugin:
            out = (
                claude_plugin.install(c, apply, a.force)
                if a.cmd == "install"
                else claude_plugin.uninstall(c, apply)
            )
        else:
            out = (
                install(c, a.scope, argv, apply, a.force)
                if a.cmd == "install"
                else uninstall(c, a.scope, apply)
            )
        results.append(out.as_dict())
    data = {"action": a.cmd, "applied": apply, "command": argv, "clients": results, "not_detected": missing}
    _emit(a.json, data, _plan_text)
    bad = any(r["status"] in ("error", "collision") for r in results)
    return 1 if bad else 0


def _plugin_mode(c) -> bool:
    try:
        return claude_plugin.state(c)["plugin"]
    except InstallError:
        return False


def _emit(as_json: bool, data: dict[str, Any], render) -> None:
    print(json.dumps(data, indent=2) if as_json else render(data))


def _plan_text(d: dict[str, Any]) -> str:
    lines = [] if d["applied"] else ["Plan only. Nothing changed. Run again with --yes to apply."]
    if d.get("command"):
        lines.append("server command: " + " ".join(d["command"]))
    for r in d["clients"]:
        lines.append(f"[{r['client']}] {r['status']}")
        lines += [f"  {s}" for s in r["steps"]] + [f"  note: {n}" for n in r["notes"]]
    if d["not_detected"]:
        lines.append("not detected (skipped): " + ", ".join(d["not_detected"]))
    return "\n".join(lines)


def _doctor_text(d: dict[str, Any]) -> str:
    lines = []
    for r in d["clients"]:
        hs = r.get("handshake") or {}
        lines.append(f"[{r['client']}] app: {'yes ' + str(r['version']) if r['present'] else 'not found'}")
        if r.get("mode") == "plugin":
            lines.append(f"  plugin: {r.get('line') or 'not listed by claude mcp list'}")
            lines.append(
                f"  connects: {'yes' if r.get('connects') else 'no'}"
                + (
                    "  WARNING: also a separate user-scope 'subtask' entry"
                    if r.get("duplicate_user_entry")
                    else ""
                )
            )
            continue
        lines.append(f"  config: {r.get('config')}  configured: {'yes' if r.get('configured') else 'no'}")
        if r.get("command"):
            lines.append(f"  command: {' '.join(r['command'])}  exists: {r.get('command_exists')}")
        conn = f"yes ({hs.get('tools')} tools, {hs.get('protocol')})" if r.get("connects") else \
            f"no ({hs.get('error') or r.get('error') or 'not configured'})"  # fmt: skip
        lines.append(f"  connects: {conn}  auth: {r.get('auth', 'not needed')}")
    if d["not_detected"]:
        lines.append("not detected: " + ", ".join(d["not_detected"]))
    return "\n".join(lines)


def install_skill(dest: str | None = None, client: str = "all") -> list[str]:
    """Copy or update only SKILL.md: into DEST/subtask/ or into each installed client's skills folder.

    A file there that is not a subtask skill (no `name: subtask`) is left alone.
    """
    from .fileops import atomic_write, read_bytes
    from .installer import skill_source

    body = skill_source()
    if dest:
        targets = [Path(dest).expanduser() / "subtask" / "SKILL.md"]
    else:
        targets = [c.skill_path("user") for c in _selected(client)[0]]
    out = []
    for path in targets:
        cur = read_bytes(path)
        if cur == body:
            out.append(f"unchanged: {path}")
        elif cur is not None and b"name: subtask" not in cur:
            out.append(f"skipped (not a subtask skill): {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, body)
            out.append(f"{'updated' if cur is not None else 'installed'}: {path}")
    return out or ["no client found; pass --dest DIR"]
