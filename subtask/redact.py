"""Remove secrets from text before it is saved or shown.

Not a guarantee: a pattern list cannot recognise every secret. It is one layer
next to a minimal environment and the isolated runner, never a replacement.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "[REDACTED]"

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("stripe", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("huggingface", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url-credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]{3,}(?=@)")),
    (
        "assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)"
            r"(\s*[=:]\s*[\"']?)([^\s\"'`]{8,})"
        ),
    ),
]

_known: set[str] = set()


def remember(values: Iterable[str]) -> None:
    """Exact secret values subtask itself loaded: always masked, whatever their shape."""
    for value in values:
        if value and len(value) >= 8:
            _known.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    for value in sorted(_known, key=len, reverse=True):
        text = text.replace(value, MASK)
    for name, pattern in PATTERNS:
        if name == "assignment":
            text = pattern.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
        elif name == "anthropic":
            text = pattern.sub("sk-ant-" + MASK, text)
        else:
            text = pattern.sub(MASK, text)
    return text
