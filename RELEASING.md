# Releasing

1. Bump `subtask/__init__.py`, the version in `server.json`, the plugin manifests, and add a CHANGELOG section.
2. Merge to `main` with CI green.
3. `git tag -a vX.Y.Z -m "subtask X.Y.Z" && git push origin vX.Y.Z`.

`.github/workflows/release.yml` then tests, builds, publishes to PyPI with Trusted Publishing (no token;
one-time setup on pypi.org: project `subtask-mcp` -> Publishing -> GitHub publisher `tedin7/subtask`,
workflow `release.yml`, environment `pypi`), creates the GitHub release with the artifacts, and
publishes `server.json` to the MCP Registry with GitHub OIDC.

Rollback: yank the version on PyPI (`Manage` -> `Yank`), delete the GitHub release, and tag a fixed
version; the registry keeps history, publish the fixed version over it.

Previous content kept below for reference.

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
