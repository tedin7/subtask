"""Two independent stdio clients exercise both backends without model calls."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from contextlib import asynccontextmanager

import pytest

from .conftest import fake_cli

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKENDS = ("subtask.fastserver", "subtask.server")


class RawClient:
    """Minimal JSON-RPC client independent of the official MCP implementation."""

    def __init__(self, proc):
        self.proc = proc
        self.serial = 0

    async def request(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.proc.stdin.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }
                )
                + "\n"
            ).encode()
        )
        await self.proc.stdin.drain()
        async with asyncio.timeout(30):
            while line := await self.proc.stdout.readline():
                response = json.loads(line)
                if response.get("id") == request_id:
                    assert "error" not in response, response
                    return response["result"]
        raise AssertionError("server exited before response")

    async def call(self, name, arguments):
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), result
        return "\n".join(c["text"] for c in result["content"] if c["type"] == "text")

    async def crash(self):
        self.proc.kill()
        await self.proc.wait()


@asynccontextmanager
async def client(backend, implementation):
    if implementation == "sdk":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", backend],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()

            class SDKClient:
                async def call(self, name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result
                    return "\n".join(c.text for c in result.content if c.type == "text")

            yield SDKClient()
        return
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        backend,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw = RawClient(proc)
    try:
        await raw.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "independent-recovery-test", "version": "1"},
            },
        )
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        await proc.stdin.drain()
        yield raw
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()


async def wait_marker(path):
    async with asyncio.timeout(15):
        while not path.exists():
            await asyncio.sleep(0.05)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("implementation", ("sdk", "raw"))
def test_stop_and_result_survive_new_client(home, backend, implementation):
    marker = home / "engine-started"
    fake_cli(home, "kimi", f"echo $$ > {shlex.quote(str(marker))}; sleep 30")

    async def exercise():
        job = None
        try:
            async with client(backend, implementation) as first:
                started = await first.call("start", {"task": "offline", "engine": "kimi", "cwd": str(home)})
                assert " started " in started, started
                job = started.split()[0]
                await wait_marker(marker)
                from subtask import procid

                engine_identity = procid.identity(int(marker.read_text()))
                assert procid.alive(engine_identity) == procid.ALIVE
                stopped = await first.call("stop", {"id": job})
                assert stopped == f"{job} stopped", stopped
                assert procid.alive(engine_identity) == procid.DEAD
            async with client(backend, implementation) as second:
                status = await second.call("status", {"id": job})
                assert " stopped " in status, status
                result = await second.call("result", {"id": job, "evidence": True})
                evidence = json.loads(result)
                assert evidence, result
            from subtask import admission, jobs

            assert jobs.status(job)["state"] == "stopped"
            assert job not in admission.active()
        finally:
            if job:
                from subtask import core

                core.stop(job)

    asyncio.run(exercise())


@pytest.mark.parametrize("backend", BACKENDS)
def test_server_crash_keeps_worker_and_result_for_other_client(home, backend):
    marker = home / "engine-started"
    release = home / "release-engine"
    fake_cli(
        home,
        "kimi",
        f"echo $$ > {shlex.quote(str(marker))}; "
        f"while [ ! -f {shlex.quote(str(release))} ]; do sleep 0.05; done; "
        "echo durable-answer",
    )

    async def exercise():
        job = None
        try:
            async with client(backend, "raw") as first:
                started = await first.call("start", {"task": "offline", "engine": "kimi", "cwd": str(home)})
                assert " started " in started, started
                job = started.split()[0]
                await wait_marker(marker)
                from subtask import procid

                engine_identity = procid.identity(int(marker.read_text()))
                assert procid.alive(engine_identity) == procid.ALIVE
                await first.crash()
                assert procid.alive(engine_identity) == procid.ALIVE
                release.touch()
            async with client(backend, "sdk") as second:
                waited = await second.call("wait", {"ids": [job], "timeout": 30})
                assert " done " in waited, waited
                result = await second.call("result", {"id": job, "full": True})
                assert "durable-answer" in result, result
            from subtask import jobs

            assert jobs.status(job)["state"] == "done"
            assert "durable-answer" in (jobs.job_dir(job) / "result.md").read_text()
        finally:
            if job:
                from subtask import core

                core.stop(job)

    asyncio.run(exercise())
