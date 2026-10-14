# Releasing

The version lives in `subtask/__init__.py` (`__version__`); packaging reads it from there.

1. Update `CHANGELOG.md` and bump `__version__`. Commit (`chore: release X.Y.Z`).
2. Make sure CI is green on `main`.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Build: `uv build`.
5. Test the wheel in a fresh venv: `uv venv /tmp/w && uv pip install --python /tmp/w/bin/python dist/*.whl && /tmp/w/bin/subtask --help`.
6. Create a GitHub release for the tag and attach `dist/*`.
7. Optional PyPI publish of `subtask-mcp` through trusted publishing. This needs the owner's PyPI account and a trusted-publisher entry for this repository. Not set up yet.

## Rollback

Yank the bad version on PyPI (if published), mark the GitHub release as pre-release or delete it, and point users at the previous tag.

## MCP Registry (not done)

1. Write `server.json` for `subtask-mcp` (name, version, package entry for PyPI).
2. Authenticate with the registry publisher CLI using the owner's GitHub account.
3. Publish, then confirm the listing. Repeat on each release.
