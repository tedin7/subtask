# Contributing

## Dev setup

```bash
git clone https://github.com/tedin7/subtask && cd subtask
uv venv && uv pip install -e '.[dev]' -c constraints.txt
```

Create the environment in each worktree. The system Python may lack the installed
`subtask-mcp` command and report misleading failures in the install tests. The
`dev` extra also installs pytest-xdist, needed for the `-n 0` option below.

Run tests from the worktree being tested. With `python -m pytest`, the current
directory takes precedence over `PYTHONPATH`, so running from another checkout
can silently import its `subtask` package. Before testing a branch, check that
the import resolves inside that worktree:

```bash
cd /path/to/worktree
.venv/bin/python -c 'from pathlib import Path; import subtask; p = Path(subtask.__file__).resolve(); print(p); assert p.is_relative_to(Path.cwd().resolve())'
.venv/bin/python -m pytest -n 0 -q tests/test_file.py
```

## Tests and lint

Run Ruff from the worktree being checked as well. Relative paths such as
`subtask tests` refer to the current directory, even when the Ruff executable
comes from another checkout. Running it from the main checkout checks that
checkout, not the worktree under review:

```bash
cd /path/to/worktree
.venv/bin/python -m pytest -n 0 -q -p no:cacheprovider     # offline, fake engines only
.venv/bin/python -m pytest -n 0 -m contract                # adapter contract tests on recorded output
uvx ruff check subtask tests
uvx ruff format --check subtask tests
```

Install `bubblewrap` to run the isolation tests (they skip without it).
Pull requests must not add tests that call a real engine or spend money. Mark such tests `live`; they are skipped by default.

## Tool schema

`subtask/tools.json` is generated. After changing a tool signature:

```bash
subtask-mcp-fastmcp --dump-tools > subtask/tools.json
```

CI fails on drift.

## Adding an engine adapter

Engines are plugins found through the `subtask.engines` entry-point group. Write an adapter
(see `subtask/engines/kimi.py` for a small one) and a factory, then declare it in your package's
`pyproject.toml` as `[project.entry-points."subtask.engines"] name = "module:factory"`, and add a contract test that parses real recorded
output from the engine (store it under `tests/fixtures`).

## Commits

`type: description`, with type one of feat, fix, refactor, docs, test, chore, perf, ci.
