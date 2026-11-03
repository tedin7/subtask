# Releasing

The version lives in `subtask/__init__.py`; packaging reads it from there.

1. Bump `__version__`, `server.json` and the plugin manifests, and add a CHANGELOG section.
2. Run the checks locally on `main`. The tag does not wait for CI:

       uv run --no-sync ruff check subtask tests
       uv run --no-sync ruff format --check subtask tests
       uv run --no-sync python -m subtask.server --dump-tools | diff - subtask/tools.json
       uv run --no-sync pytest -q -p no:cacheprovider

   Also check the `ci.yml` workflow on `main`: it runs on pushes to `main` and on pull requests.
3. Create and push an annotated tag: `git tag -a vX.Y.Z -m "subtask X.Y.Z"` and
   `git push origin vX.Y.Z`.

The tag triggers `.github/workflows/release.yml`. It checks the package and server
versions against the tag, builds the distribution, publishes to PyPI, creates a
GitHub release, and publishes `server.json` to the MCP Registry. It does **not**
run tests or wait for CI; verify those before tagging. Publishing requires the
configured PyPI trusted publisher and GitHub environment `pypi`.

## Rollback

If published, yank the bad PyPI version and mark the GitHub release as a
pre-release or delete it. Point users to the previous tag, then release a fixed
version. The MCP Registry retains its history.
