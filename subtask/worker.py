"""Run one job in the background. Started detached by `subtask start`/`reply`.

python -m subtask.worker JOB_ID [--reply-file FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config, jobs

# engine -> (kind, model). "api" pays with the API key, "sub" with the CLI login.
ENGINES = {
    'sonnet-api': ('api', 'sonnet'),
    'haiku-api': ('api', 'haiku'),
    'opus-api': ('api', 'opus'),
    'claude': ('sub', None),
    'codex': ('codex', None),
    'kimi': ('kimi', None),
    'opencode': ('opencode', None),
}

SUMMARY_RULE = (
    '\n\nWhen you finish, end your answer with a section that starts with the exact '
    'line "## Summary" and has at most 10 short lines: outcome, files touched, '
    'commits, tests run, what is left. Write it in the language of the task.'
)
# OS-level sandbox for Bash: writes outside the working dir are blocked.
SANDBOX = {'enabled': True, 'allowUnsandboxedCommands': False, 'failIfUnavailable': True}
READONLY_TOOLS = ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch']


def _api_env(kind: str) -> dict[str, str]:
    env = {'IS_SANDBOX': '1'}  # bypassPermissions is refused as root without it
    if kind == 'api':
        secrets = config.load_secrets()
        if not secrets.get('ANTHROPIC_API_KEY'):
            raise RuntimeError(f'ANTHROPIC_API_KEY missing (file {config.ENV_FILE})')
        env['ANTHROPIC_API_KEY'] = secrets['ANTHROPIC_API_KEY']
        if secrets.get('ANTHROPIC_WORKSPACE_ID'):
            env['ANTHROPIC_CUSTOM_HEADERS'] = f'anthropic-workspace-id: {secrets["ANTHROPIC_WORKSPACE_ID"]}'
    else:
        env['ANTHROPIC_API_KEY'] = ''  # force the subscription login
    return env


async def _run_claude(job_id: str, spec: dict, prompt: str, resume: str | None) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        ToolUseBlock,
        query,
    )

    kind, model = ENGINES[spec['engine']]
    readonly = spec.get('readonly', False)
    options = ClaudeAgentOptions(
        cwd=spec['cwd'],
        model=spec.get('model') or model,
        max_turns=spec['max_turns'],
        max_budget_usd=spec['max_usd'] if kind == 'api' else None,
        effort=spec['effort'],
        permission_mode='dontAsk' if readonly else 'bypassPermissions',
        allowed_tools=READONLY_TOOLS if readonly else [],
        disallowed_tools=['mcp__*'],
        sandbox=None if readonly or not spec.get('sandbox', True) else SANDBOX,
        system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': SUMMARY_RULE},
        env=_api_env(kind),
        resume=resume,
    )
    turns = 0
    final: dict = {}
    log = (jobs.job_dir(job_id) / 'events.jsonl').open('a')
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            turns += 1
            tools = [b.name for b in message.content if isinstance(b, ToolUseBlock)]
            if tools:
                jobs.update_status(job_id, turns=turns, last=', '.join(tools)[:80])
                log.write(json.dumps({'turn': turns, 'tools': tools}) + '\n')
                log.flush()
        elif isinstance(message, ResultMessage):
            final = {
                'ok': not message.is_error and message.subtype == 'success',
                'subtype': message.subtype,
                'result': message.result or '',
                'cost_usd': message.total_cost_usd or 0.0,
                'session_id': message.session_id,
                'turns': message.num_turns,
            }
    return final


def _run_cli(job_id: str, spec: dict, prompt: str, resume: str | None) -> dict:
    engine, cwd = spec['engine'], spec['cwd']
    last = jobs.job_dir(job_id) / 'last-message.txt'
    if engine == 'codex':
        argv = [
            'codex',
            'exec',
            '--json',
            '--skip-git-repo-check',
            '-C',
            cwd,
            '--dangerously-bypass-approvals-and-sandbox',
            '-o',
            str(last),
        ]
        argv += ['resume', resume, prompt] if resume else [prompt]
    elif engine == 'kimi':
        argv = ['kimi', '--auto', '--output-format', 'text', '-p', prompt]
        argv[1:1] = ['-c'] if resume else []
    elif engine == 'opencode':
        argv = ['opencode', 'run', prompt] + (['--continue'] if resume else [])
    else:
        raise RuntimeError(f'unknown engine: {engine}')
    with (jobs.job_dir(job_id) / 'output.log').open('a') as out:
        proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=out, text=True, check=False)
    text = last.read_text() if last.is_file() else proc.stdout
    session = resume or 'continue'
    if engine == 'codex':
        for line in proc.stdout.splitlines():
            if '"thread_id"' in line:
                session = json.loads(line).get('thread_id', session)
                break
    return {
        'ok': proc.returncode == 0,
        'subtype': f'exit {proc.returncode}',
        'result': text[-20000:],
        'cost_usd': 0.0,
        'session_id': session,
        'turns': None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('job_id')
    parser.add_argument('--reply-file')
    args = parser.parse_args()
    job_id = args.job_id
    spec = jobs.spec(job_id)
    previous = jobs.status(job_id)
    prompt = Path(args.reply_file).read_text() if args.reply_file else spec['task']
    resume = previous.get('session_id') if args.reply_file else None
    kind = ENGINES[spec['engine']][0]
    jobs.update_status(job_id, state='running', pid=os.getpid(), last='starting', error=None)
    if kind == 'api' and jobs.reserve(job_id, spec['max_usd']) is None:
        jobs.update_status(job_id, state='failed', error=f'monthly cap {config.MONTHLY_CAP_USD} $ reached')
        return 1
    final: dict = {}
    try:
        if kind in ('api', 'sub'):
            final = asyncio.run(_run_claude(job_id, spec, prompt, resume))
        else:
            final = _run_cli(job_id, spec, prompt, resume)
    except Exception as exc:
        final = {'ok': False, 'subtype': 'exception', 'result': f'{type(exc).__name__}: {exc}', 'cost_usd': 0.0}
    finally:
        cost = float(final.get('cost_usd') or 0)
        total = float(previous.get('cost_usd') or 0) + cost if args.reply_file else cost
        if kind == 'api':
            jobs.settle(job_id, cost)
        (jobs.job_dir(job_id) / 'result.md').write_text(final.get('result', ''))
        jobs.update_status(
            job_id,
            state='done' if final.get('ok') else 'failed',
            cost_usd=round(total, 4),
            session_id=final.get('session_id') or previous.get('session_id'),
            turns=final.get('turns'),
            detail=final.get('subtype'),
            pid=None,
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
