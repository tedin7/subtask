"""Source of bench/corpus/manifest.json (v1, frozen 2026-10-14). Re-running it drops the
`validated` blocks: run bench/validate_corpus.py right after. Changing a task changes corpus_hash.

    python bench/corpus/build_manifest.py > bench/corpus/manifest.json && python bench/validate_corpus.py
"""

import json

BASE = "3f731f60ebcacba35c0d92f6cc4b917b836517ce"
B = {"read": (0.5, 10), "review": (1.0, 15), "code": (1.5, 20), "hard": (3.0, 40)}


def T(id, kind, split, deleg, prompt, files, acceptance, note=""):
    usd, mins = B[kind]
    t = {
        "id": id,
        "kind": kind,
        "split": split,
        "delegation_expected": deleg,
        "prompt": prompt,
        "files": files,
        "acceptance": acceptance,
        "budget": {"max_usd": usd, "max_minutes": mins},
    }
    if note:
        t["why_delegation"] = note
    return t


def RA(answer, facts, forbidden=()):
    return {
        "type": "reference_answer",
        "reference": answer,
        "required_facts": [{"name": n, "regex": r} for n, r in facts],
        "forbidden": [{"name": n, "regex": r} for n, r in forbidden],
        "rubric": "score = matched required facts / total (case-insensitive regex on the final answer); "
        "verified success = score 1.0 and no forbidden match; no LLM judge",
    }


MNM = ["bench_acceptance/", "tests/", "pyproject.toml"]


def AT(test, extra_mnm=()):
    return {
        "type": "hidden_test",
        "test": f"bench/corpus/acceptance/{test}",
        "helpers": ["bench/corpus/acceptance/_cli_argv.py"]
        if test in ("test_c3_kimi_prompt.py", "test_c4_codex_sandbox.py")
        else [],
        "run": "python -m pytest -q -p no:cacheprovider bench_acceptance/" + test,
        "must_not_modify": [p for p in MNM if p not in extra_mnm],
        "regressions": "python -m pytest -q -p no:cacheprovider tests",
        "reference_patch": f"bench/corpus/reference/{test.split('_')[1]}.patch",
    }


tasks = [
    T(
        "r1",
        "read",
        "calibration",
        True,
        "In this repository, which engines does auto-routing consider for a task of kind 'review', in which order, and what tie-break rule does pick() use between them?",
        ["subtask/capacity.py"],
        RA(
            "KINDS['review'] = sonnet-api, codex, kimi; score = free - rank*5, engines under 10% free skipped, API engines skipped when the monthly room is below the job budget.",
            [
                ("order", r"sonnet-api.{0,80}codex.{0,80}kimi"),
                ("rank_penalty", r"rank\s*\*\s*5|5\s*(points|per rank)|minus\s*5"),
                ("ten_pct", r"10\s*%|< ?10\b|below 10"),
            ],
        ),
    ),
    T(
        "r2",
        "read",
        "test",
        False,
        "What is the TTL of the engine capacity cache, and in which file is it stored?",
        ["subtask/capacity.py"],
        RA(
            "120 seconds, in capacity.json under SUBTASK_HOME.",
            [("ttl", r"\b120\b"), ("file", r"capacity\.json")],
        ),
        "one constant lookup: the brief is as long as the work",
    ),
    T(
        "r3",
        "read",
        "test",
        True,
        "How does jobs._alive decide that a worker pid is still the same subtask worker and not a reused pid? Cite the mechanism precisely.",
        ["subtask/jobs.py"],
        RA(
            "It reaps its own exited children with waitpid(WNOHANG), then reads /proc/<pid>/cmdline and requires both 'subtask.worker' and the job id in it; OSError other than FileNotFoundError is treated as alive.",
            [
                ("proc_cmdline", r"/proc/.{0,12}cmdline"),
                ("module_marker", r"subtask\.worker"),
                ("job_id", r"job[ _-]?id"),
                ("waitpid", r"waitpid|WNOHANG|reap"),
            ],
        ),
    ),
    T(
        "r4",
        "read",
        "calibration",
        True,
        "Trace what happens to money and job state when a worker process dies before finishing. Which function charges what?",
        ["subtask/jobs.py", "subtask/core.py"],
        RA(
            "jobs.status() sees a queued/running job whose pid is dead, takes the job lock, re-checks, calls settle_open(job, max_usd) which writes a cost row for the full max_usd if a reservation is open, then marks the job failed with 'worker died before finishing'.",
            [
                ("settle_open", r"settle_open"),
                ("max_usd", r"max_usd|full budget|whole budget"),
                ("failed", r"failed"),
                ("message", r"worker died before finishing"),
                ("lock", r"job[_ ]lock|lock"),
            ],
        ),
    ),
    T(
        "r5",
        "read",
        "test",
        False,
        "Which MCP protocol versions does fastserver accept in initialize, and what does it answer when the client asks for an unknown one?",
        ["subtask/fastserver.py"],
        RA(
            "2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05; unknown -> it answers with the first (2025-11-25).",
            [
                ("v1", r"2025-11-25"),
                ("v2", r"2025-06-18"),
                ("v3", r"2025-03-26"),
                ("v4", r"2024-11-05"),
                (
                    "fallback",
                    r"(first|newest|latest|default).{0,40}2025-11-25|2025-11-25.{0,60}(fallback|unknown|otherwise|default)",
                ),
            ],
        ),
        "single-file lookup, answer is one line",
    ),
    T(
        "r6",
        "read",
        "test",
        True,
        "Explain end to end how the Anthropic API key reaches the Claude agent run by a sub-job, and what keeps it out of the agent's environment and Bash commands.",
        ["subtask/worker.py", "subtask/config.py"],
        RA(
            "load_secrets reads the env file (never os.environ); _key_helper writes key-helper.sh that prints it; settings set apiKeyHelper; _api_env sets ANTHROPIC_API_KEY to empty; scrub_environ deletes secret-looking env vars; deny rules + sandbox denyRead cover the env file, helper and credentials.",
            [
                ("helper", r"apiKeyHelper|key-helper"),
                ("env_file", r"load_secrets|env file|SUBTASK_ENV_FILE"),
                ("empty_env", r"ANTHROPIC_API_KEY.{0,40}(empty|\"\"|blank)"),
                ("scrub", r"scrub_environ"),
                ("deny", r"deny(Read)?|denyRead"),
            ],
        ),
    ),
    T(
        "v1",
        "review",
        "calibration",
        True,
        "Review subtask/server.py for correctness bugs in how tool schemas are published. Report each defect with the function, the effect on clients, and a fix.",
        ["subtask/server.py", "subtask/tools.json"],
        RA(
            "_slim() drops every key named 'title', including the real property 'title' of the step tool: step's schema lists 'title' as required but has no such property; tools.json has the same hole.",
            [
                ("fn", r"_slim"),
                ("title_prop", r"title"),
                ("step", r"\bstep\b"),
                (
                    "required_mismatch",
                    r"required|missing.{0,30}propert|propert.{0,30}(missing|dropped|removed)",
                ),
            ],
        ),
    ),
    T(
        "v2",
        "review",
        "test",
        True,
        "Review the admission logic of core.start() for concurrency problems when several MCP servers or CLI processes start jobs at the same time.",
        ["subtask/core.py", "subtask/jobs.py"],
        RA(
            "running_count() >= MAX_RUNNING is checked, then the job is created later without any lock: check-then-act race across processes, more than MAX_RUNNING jobs can start; fix with a file lock around count+claim.",
            [
                ("check", r"running_count|MAX_RUNNING"),
                ("race", r"race|TOCTOU|check[- ]then[- ]act|atomic"),
                ("processes", r"process|concurren|parallel"),
                ("lock", r"lock|flock"),
            ],
        ),
    ),
    T(
        "v3",
        "review",
        "test",
        True,
        "Review worker._run_cli for compatibility with current engine CLIs. Kimi CLI 2.1 is installed. What breaks?",
        ["subtask/worker.py"],
        RA(
            "The write path builds kimi --auto ... -p prompt; Kimi 2.1 rejects --prompt combined with --auto ('Cannot combine --prompt with --auto'), so every write job fails; drop --auto, prompt mode already runs tools.",
            [
                ("auto", r"--auto"),
                ("prompt", r"-p\b|--prompt"),
                ("reject", r"reject|cannot combine|incompatib|not allowed|error|fails?"),
            ],
        ),
    ),
    T(
        "v4",
        "review",
        "calibration",
        False,
        "Review fastserver.initialize: is the serverInfo it reports correct for this package?",
        ["subtask/fastserver.py", "pyproject.toml"],
        RA(
            "serverInfo.version is hard-coded '0.2.0' but pyproject declares 0.1.0.",
            [("v020", r"0\.2\.0"), ("v010", r"0\.1\.0"), ("where", r"serverInfo|version")],
        ),
        "two-file glance; cheaper to check than to brief",
    ),
    T(
        "v5",
        "review",
        "test",
        True,
        "Security review: does the redaction applied to job results protect against secrets other than Anthropic keys leaking into results?",
        ["subtask/worker.py"],
        RA(
            "KEY_RE only matches sk-ant- keys; OpenAI/GitHub/JWT/AWS/private keys/password=... pass through unredacted into result.md and replies.",
            [
                ("only_ant", r"(only|just).{0,40}sk-ant|sk-ant.{0,60}only"),
                ("others", r"openai|github|ghp_|jwt|aws|AKIA|private key|password|deepseek"),
            ],
        ),
    ),
    T(
        "v6",
        "review",
        "test",
        True,
        "Security review of the codex engine path in worker._run_cli: what sandbox does a write job run with?",
        ["subtask/worker.py"],
        RA(
            "Write jobs pass --dangerously-bypass-approvals-and-sandbox: no codex sandbox at all, commands run unsandboxed as the user; use -s workspace-write with approval_policy=never.",
            [
                ("flag", r"dangerously-bypass-approvals-and-sandbox"),
                ("no_sandbox", r"no sandbox|without (a |any )?sandbox|unsandboxed|bypass"),
                ("fix", r"workspace-write"),
            ],
        ),
    ),
    T(
        "c1",
        "code",
        "calibration",
        True,
        "Fix server._slim so that it drops schema 'title' metadata but keeps a real tool parameter named 'title' (the step tool's title). Keep subtask/tools.json in line with what FastMCP publishes.",
        ["subtask/server.py"],
        AT("test_c1_slim_title.py"),
    ),
    T(
        "c2",
        "code",
        "test",
        False,
        "fastserver reports serverInfo version 0.2.0 but the package is 0.1.0. Make it report the package version.",
        ["subtask/fastserver.py"],
        AT("test_c2_server_version.py"),
        "one-line edit",
    ),
    T(
        "c3",
        "code",
        "test",
        True,
        "Kimi CLI 2.1 refuses --auto together with -p/--prompt. Fix the kimi argv in worker._run_cli so write jobs (and resumed ones) keep the prompt without --auto.",
        ["subtask/worker.py"],
        AT("test_c3_kimi_prompt.py"),
    ),
    T(
        "c4",
        "code",
        "calibration",
        True,
        "Codex write jobs run with --dangerously-bypass-approvals-and-sandbox. Make them use codex's workspace-write sandbox with approvals never asked; keep read-only jobs as they are.",
        ["subtask/worker.py"],
        AT("test_c4_codex_sandbox.py"),
    ),
    T(
        "c5",
        "code",
        "test",
        False,
        "core.start accepts max_turns=True because bool is an int. Reject booleans with the existing max_turns error.",
        ["subtask/core.py"],
        AT("test_c5_max_turns_bool.py"),
        "one condition; needs no exploration",
    ),
    T(
        "c6",
        "code",
        "test",
        False,
        "core.wait([]) sleeps until the timeout. Make it return at once with a message saying no job ids were given.",
        ["subtask/core.py"],
        AT("test_c6_wait_empty.py"),
        "two lines; the caller already knows exactly where",
    ),
    T(
        "h1",
        "hard",
        "calibration",
        True,
        "SUBTASK_MAX_RUNNING is checked then acted on in core.start without a lock, so parallel processes overshoot it. Make admission atomic across processes (file lock), without holding the lock longer than needed.",
        ["subtask/core.py", "subtask/jobs.py"],
        AT("test_h1_admission_race.py"),
    ),
    T(
        "h2",
        "hard",
        "test",
        True,
        "Redaction only masks sk-ant keys. Extend worker.redact to mask OpenAI-style sk-/sk-proj- keys, GitHub tokens (ghp_, github_pat_), JWTs, AWS access key ids, PEM private key blocks and key=value secrets (password, secret, token, api_key), without touching ordinary text.",
        ["subtask/worker.py"],
        AT("test_h2_redaction.py"),
    ),
    T(
        "h3",
        "hard",
        "test",
        True,
        "If the worker cannot be spawned after the git worktree was created, core.start leaves the worktree behind. Roll it back so a failed start leaves no worktree and the job is marked failed.",
        ["subtask/core.py"],
        AT("test_h3_spawn_rollback.py"),
    ),
    T(
        "h4",
        "hard",
        "calibration",
        True,
        "Money is reserved by the worker after the job is already admitted, so a start over the monthly cap creates a job folder and worktree and spawns a worker that then fails. Move the first-attempt reservation into core.start at admission (refuse before creating anything when the cap would be exceeded, release it if the start fails later); the worker must not reserve again on a first attempt (replies still reserve).",
        ["subtask/core.py", "subtask/worker.py", "subtask/jobs.py"],
        AT("test_h4_reserve_at_admission.py"),
    ),
    T(
        "h5",
        "hard",
        "test",
        True,
        "Make the version single-sourced: subtask.__version__ equals pyproject's version (0.1.0), `python -m subtask.cli --version` prints it, and fastserver serverInfo reports it.",
        ["subtask/__init__.py", "subtask/cli.py", "subtask/fastserver.py", "pyproject.toml"],
        AT("test_h5_version_single_source.py", extra_mnm=("pyproject.toml",)),
    ),
    T(
        "h6",
        "hard",
        "test",
        True,
        "A reservation made last month and still open is ignored by the monthly total, so the cap can be exceeded across a month boundary. Count open reservations whatever month they were made in, and only this month's settled costs.",
        ["subtask/jobs.py"],
        AT("test_h6_ledger_month_boundary.py"),
    ),
]
man = {
    "v": 1,
    "name": "subtask-e2e-pilot",
    "frozen": "2026-10-14",
    "target": {
        "repo": "self (the subtask git repository)",
        "commit": BASE,
        "baseline_suite": "python -m pytest -q -p no:cacheprovider tests",
    },
    "splits": {
        "calibration": "2 per kind, for tuning prompts/thresholds; never reported as evidence",
        "test": "4 per kind, reported",
    },
    "tasks": tasks,
}
print(json.dumps(man, indent=1, ensure_ascii=False))
