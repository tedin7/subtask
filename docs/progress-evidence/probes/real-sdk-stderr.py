"""Manual offline diagnostic: real SDK/CLI, intentional startup flag error."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import sys
import tempfile
import threading
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outer-netns", required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current_netns = os.readlink("/proc/self/ns/net")
    assert current_netns != args.outer_netns, "A separate network namespace is required"
    routes = [row for row in Path("/proc/self/net/route").read_text().splitlines() if row.strip()]
    assert not routes or (len(routes) == 1 and routes[0].startswith("Iface")), "Unexpected network route"
    sys.path.insert(0, str(Path.cwd()))
    import claude_agent_sdk as sdk

    from subtask.engines.base import RunContext

    spec = importlib.util.spec_from_file_location("subtask.engines._offline_stderr_probe", args.adapter)
    assert spec and spec.loader
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    output = args.output.resolve()
    output.mkdir(mode=0o700, exist_ok=False)
    real_query = sdk.query
    flag = "fixture-offline-stderr-unknown-option"

    async def invalid_startup(*, prompt, options):
        options.extra_args = {flag: None}
        async for message in real_query(prompt=prompt, options=options):
            yield message

    with tempfile.TemporaryDirectory(prefix="subtask-sdk-stderr-") as temp:
        isolated = Path(temp)
        adapter.settings = lambda *unused: "{}"
        adapter.api_env = lambda: {
            "ANTHROPIC_API_KEY": "fixture-only-not-a-provider-key",
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "ANTHROPIC_CUSTOM_HEADERS": "",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
            "CLAUDE_CONFIG_DIR": str(isolated / "config"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        sdk.query = invalid_startup
        ctx = RunContext(
            job_id="offline-real-sdk-probe",
            spec={"cwd": temp, "readonly": True, "max_turns": 1, "max_usd": 0.01, "effort": "medium"},
            prompt="Offline startup diagnostic; no model turn is allowed.",
            resume=None,
            attempt=1,
            folder=output,
            emit=lambda *unused, **unused_kw: None,
            started=threading.Event(),
            wrap=lambda argv, env: (argv, env),
            env={},
        )
        failure = None
        try:
            asyncio.run(adapter.ClaudeApi("sonnet-api", "sonnet")._run(ctx))
        except sdk.ProcessError as exc:
            failure = {"type": type(exc).__name__, "exit_code": exc.exit_code}
        finally:
            sdk.query = real_query
        assert failure and failure["exit_code"] == 1, "Expected native CLI startup failure"
        assert not ctx.started.is_set(), "Unexpected SDK message/turn"
        log = output / "worker-stderr.log"
        raw = log.read_bytes()
        assert flag.encode() in raw and b"unknown option" in raw
        assert len(raw) <= 32768
        result = {
            "sdk_version": importlib.metadata.version("claude-agent-sdk"),
            "adapter_sha256": hashlib.sha256(args.adapter.read_bytes()).hexdigest(),
            "outer_netns": args.outer_netns,
            "inner_netns": current_netns,
            "failure": failure,
            "stderr_bytes": len(raw),
            "stderr_sha256": hashlib.sha256(raw).hexdigest(),
            "native_unknown_option_captured": True,
            "provider_calls": 0,
            "historical_failure_cause_proven": False,
        }
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
