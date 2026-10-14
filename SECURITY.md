# Security policy

## Supported versions

Only the latest minor release receives fixes.

## Reporting a vulnerability

Report privately with GitHub Security Advisories: on the repository, open the Security tab and
click "Report a vulnerability". Please do not open a public issue for security problems.

## Threat model (summary)

- A git worktree is a convenience for isolating edits, not a sandbox.
- The default sandbox runs as the same user as the caller. It limits mistakes, not a hostile engine.
- The `isolated` mode uses bubblewrap (Linux only) and is the only mode meant to contain an untrusted engine.
- Secret redaction in logs and results is best effort. Do not rely on it for secrets you cannot rotate.
