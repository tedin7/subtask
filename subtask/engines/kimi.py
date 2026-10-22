"""Kimi Code CLI (`kimi -p --output-format stream-json`). Tested with 2.1.1.

Kimi 2.1 refuses `--prompt` together with `--auto` ("Cannot combine --prompt
with --auto"); prompt mode already runs tools without asking.
"""

from __future__ import annotations

import json
import math
import os
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .base import Capabilities, Capacity, CliAdapter, RunContext, describe_command, json_line, short_command


def kimi_home() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")


def _managed_account() -> tuple[str, str]:
    """Read only the configured managed account; never send its token to a custom host."""
    home = kimi_home()
    settings = tomllib.loads((home / "config.toml").read_text())
    providers = settings.get("providers", {})
    if not isinstance(providers, dict):
        raise TypeError("invalid managed Kimi configuration")
    provider = providers.get("managed:kimi-code", {})
    if not isinstance(provider, dict):
        raise TypeError("invalid managed Kimi configuration")
    base = provider.get("base_url", "")
    if not isinstance(base, str):
        raise TypeError("invalid managed Kimi endpoint")
    base = base.rstrip("/")
    if base not in ("https://api.kimi.ai/coding/v1", "https://api.kimi.com/coding/v1"):
        raise ValueError("managed Kimi quota endpoint not configured")
    oauth = provider.get("oauth", {})
    if not isinstance(oauth, dict):
        raise TypeError("invalid managed Kimi authentication")
    key = oauth.get("key", "")
    if oauth.get("storage") != "file" or not isinstance(key, str) or not key.startswith("oauth/"):
        raise ValueError("managed Kimi file authentication not configured")
    name = key.removeprefix("oauth/")
    if not name or Path(name).name != name or "\\" in name or name in (".", ".."):
        raise ValueError("invalid managed Kimi credential reference")
    credentials = json.loads((home / "credentials" / f"{name}.json").read_text())
    if not isinstance(credentials, dict):
        raise TypeError("invalid managed Kimi credentials")
    token = credentials.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("managed Kimi authentication missing")
    return base, token


def _reset(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def parse_capacity(payload: dict) -> Capacity:
    """Use the tightest reported window; missing or malformed windows are not free quota."""
    windows: list[tuple[float, str, float | None]] = []
    usages = payload.get("usages")
    if isinstance(usages, dict):
        for name in ("limit_5h", "limit_7d", "month_total", "month_code", "monthTotal", "monthCode"):
            detail = usages.get(name)
            if detail is None:
                continue
            if not isinstance(detail, dict):
                raise TypeError("invalid Kimi quota window")
            ratio = detail.get("used_ratio")
            if (
                isinstance(ratio, bool)
                or not isinstance(ratio, (int, float))
                or not math.isfinite(ratio)
                or not 0 <= ratio <= 1
            ):
                raise ValueError("invalid Kimi quota ratio")
            windows.append((100 * (1 - ratio), name, _reset(detail.get("reset_time"))))
    if not windows or "usage" in payload or "limits" in payload:
        # Older services return counters instead of normalized ratios.
        details = [("week", payload.get("usage"))]
        limits = payload.get("limits", [])
        if not isinstance(limits, list):
            raise ValueError("invalid Kimi quota limits")
        details.extend(
            (f"window-{i + 1}", row.get("detail")) for i, row in enumerate(limits) if isinstance(row, dict)
        )
        for name, detail in details:
            if not isinstance(detail, dict):
                continue
            raw_limit, raw_remaining = detail.get("limit"), detail.get("remaining")
            if isinstance(raw_limit, bool) or isinstance(raw_remaining, bool):
                raise TypeError("invalid Kimi quota counters")
            limit, remaining = float(raw_limit), float(raw_remaining)
            if (
                not math.isfinite(limit)
                or not math.isfinite(remaining)
                or limit <= 0
                or not 0 <= remaining <= limit
            ):
                raise ValueError("invalid Kimi quota counters")
            windows.append((100 * remaining / limit, name, _reset(detail.get("resetTime"))))
    if not windows:
        raise ValueError("Kimi quota windows missing")
    free, _, reset = min(windows, key=lambda row: row[0])
    note = "; ".join(f"{name} {value:.1f}% free" for value, name, _ in windows)
    return Capacity(free, "pct_free", "Kimi managed /usages", time.time(), "high", note, reset)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Kimi(CliAdapter):
    name = "kimi"
    family = "kimi"
    binary = "kimi"
    supported = "kimi >=2.1"
    capabilities = Capabilities(
        readonly=False,  # --plan is a mode, not an enforced read-only boundary
        levels=("worktree", "isolated"),
        command_network_off=False,
        cost="none",
        resume=True,
        structured_events=True,
        paid=False,
        notes="no engine sandbox: use level=isolated for a filesystem boundary",
    )

    def help_args(self) -> list[str]:
        return ["--help"]

    def required_flags(self) -> list[str]:
        return ["--output-format", "--prompt", "--session"]

    def auth_state(self) -> str:
        try:
            _managed_account()
            return "present"
        except (OSError, ValueError, TypeError, KeyError):
            return "unknown"

    def capacity(self) -> Capacity:
        try:
            base, token = _managed_account()
            request = urllib.request.Request(
                base + "/usages",
                headers={
                    "Authorization": "Bearer " + token,
                    "User-Agent": "KimiCLI/2.2.0 subtask-mcp",
                },
            )
            # Refuse redirects so a quota response cannot forward the bearer credential.
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=10) as response:
                raw = response.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                raise ValueError("Kimi quota response too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("invalid Kimi quota response")
            return parse_capacity(payload)
        except urllib.error.HTTPError as exc:
            note = f"Kimi quota HTTP {exc.code}; authentication or service unavailable"
        except (OSError, ValueError, TypeError, KeyError):
            # Do not put provider bodies, configuration values or tokens in tool output.
            note = "Kimi quota unavailable; authentication, network or response not verified"
        return Capacity(None, "pct_free", "Kimi managed /usages", time.time(), "none", note)

    def run(self, ctx: RunContext):
        if not ctx.resume:
            # Invocation-only override: global settings and existing sessions stay untouched.
            ctx.env = {**ctx.env, "KIMI_MODEL_THINKING_EFFORT": ctx.spec.get("effort") or "low"}
        return super().run(ctx)

    def argv(self, ctx: RunContext) -> list[str]:
        argv = ["kimi", "--output-format", "stream-json"]
        if ctx.spec.get("model"):
            argv += ["-m", ctx.spec["model"]]
        elif not ctx.resume:
            argv += ["-m", "kimi-code/k3-256k"]
        if ctx.resume:
            argv += ["-S", ctx.resume] if ctx.resume != "continue" else ["-c"]
        return argv + ["-p", ctx.prompt]

    def parse(self, line: str, state: dict) -> str | None:
        event = json_line(line)
        if not event:
            return None
        role = event.get("role")
        if role == "meta" and event.get("type") == "session.resume_hint":
            state["session"] = event.get("session_id")
        elif role == "assistant":
            if event.get("content"):
                content = event["content"]
                state["text"].append(content if isinstance(content, str) else json.dumps(content))
            for call in event.get("tool_calls") or []:
                fn = call.get("function") or {}
                name = fn.get("name", "tool")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                state.setdefault("pending", {})[call.get("id")] = args.get("command")
                if args.get("command"):
                    return describe_command(short_command(args["command"]))
                target = args.get("path") or args.get("file_path") or args.get("pattern") or ""
                return f"{name} {Path(str(target)).name}".strip()
        elif role == "tool":
            command = state.get("pending", {}).pop(event.get("tool_call_id"), None)
            if command:
                state["commands"].append({"command": short_command(command), "exit_code": None})
        return None
