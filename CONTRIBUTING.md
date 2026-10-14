# Contributing

## Dev setup

```bash
git clone https://github.com/tedin7/subtask && cd subtask
uv venv && uv pip install -e '.[dev]' -c constraints.txt
```

## Tests and lint

```bash
.venv/bin/pytest -q -p no:cacheprovider     # offline, fake engines only
.venv/bin/pytest -m contract                # adapter contract tests on recorded output
uvx ruff check subtask tests
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
