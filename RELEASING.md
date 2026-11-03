# Releasing

Before tagging, run the checks locally on `main` (the `ci` workflow runs the same
steps on every push, but a tag does not wait for it):

    uv run --no-sync ruff check subtask tests
    uv run --no-sync ruff format --check subtask tests
    uv run --no-sync python -m subtask.server --dump-tools | diff - subtask/tools.json
    uv run --no-sync pytest -q -p no:cacheprovider

1. Bump `subtask/__init__.py`, the version in `server.json`, the plugin manifests, and add a CHANGELOG section.
2. Merge to `main` with CI green.
3. `git tag -a vX.Y.Z -m "subtask X.Y.Z" && git push origin vX.Y.Z`.

`.github/workflows/release.yml` then builds, publishes to PyPI with Trusted Publishing (no token;
one-time setup on pypi.org: project `subtask-mcp` -> Publishing -> GitHub publisher `tedin7/subtask`,
workflow `release.yml`, environment `pypi`), creates the GitHub release with the artifacts, and
publishes `server.json` to the MCP Registry with GitHub OIDC.

Rollback: yank the version on PyPI (`Manage` -> `Yank`), delete the GitHub release, and tag a fixed
version; the registry keeps history, publish the fixed version over it.
