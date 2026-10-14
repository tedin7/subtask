"""Semantic checks for published tool schemas, independent of how they were generated.

The slimming in server.py once dropped every key named "title", including the
real `step.title` parameter: the published schema then required a property it
never declared. A parity test could not see it, because both sides went
through the same transformation. These checks look at the meaning instead.
"""

from __future__ import annotations

from typing import Any

TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}
MAX_DEPTH = 32


def problems(schema: Any, where: str = "$") -> list[str]:
    """Every semantic defect found in a JSON Schema object (2020-12 subset)."""
    out: list[str] = []
    _walk(schema, schema, where, out, 0)
    return out


def _resolve(root: dict, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"non-local $ref not allowed: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise KeyError(ref)
        node = node[part]
    return node


def _declared(root: dict, schema: dict, seen: frozenset = frozenset()) -> set[str] | None:
    """Property names an object schema declares, following $ref and allOf. None = open."""
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return set()
        return _declared(root, _resolve(root, ref), seen | {ref})
    names = set(schema.get("properties", {}))
    for sub in schema.get("allOf", []):
        if isinstance(sub, dict):
            more = _declared(root, sub, seen)
            if more is None:
                return None
            names |= more
    if schema.get("patternProperties"):
        return None  # required names may be matched by a pattern: cannot judge
    return names


def _walk(root: Any, node: Any, where: str, out: list[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        out.append(f"{where}: deeper than {MAX_DEPTH}")
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            _walk(root, item, f"{where}[{i}]", out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if "$ref" in node:
        try:
            _resolve(root, node["$ref"])
        except (KeyError, ValueError) as exc:
            out.append(f"{where}: unresolved $ref {exc}")
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind else []
    for k in kinds:
        if k not in TYPES:
            out.append(f"{where}: unknown type {k!r}")
    if "properties" in node and not isinstance(node["properties"], dict):
        out.append(f"{where}: properties is not an object")
    required = node.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            out.append(f"{where}: required must be a list of names")
        else:
            declared = _declared(root, node) if isinstance(root, dict) else set()
            if declared is not None:
                missing = [r for r in required if r not in declared]
                # A required name nobody declares is a contract bug, closed or open:
                # clients build forms and calls from `properties`.
                for name in missing:
                    out.append(f"{where}: required {name!r} not declared in properties")
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            for name, sub in value.items():
                _walk(root, sub, f"{where}.properties.{name}", out, depth + 1)
        elif key in ("items", "additionalProperties", "not", "if", "then", "else", "contains") or key in (
            "anyOf",
            "oneOf",
            "allOf",
            "prefixItems",
        ):
            _walk(root, value, f"{where}.{key}", out, depth + 1)
        elif key in ("$defs", "definitions") and isinstance(value, dict):
            for name, sub in value.items():
                _walk(root, sub, f"{where}.{key}.{name}", out, depth + 1)
    if "default" in node and kinds and not _matches(node["default"], kinds):
        out.append(f"{where}: default {node['default']!r} is not {kinds}")


def _matches(value: Any, kinds: list[str]) -> bool:
    checks = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "null": lambda v: v is None,
    }
    return any(checks[k](value) for k in kinds if k in checks)


def tool_problems(tools: list[dict]) -> list[str]:
    out: list[str] = []
    names = [t.get("name") for t in tools]
    for dup in {n for n in names if names.count(n) > 1}:
        out.append(f"duplicate tool {dup}")
    for tool in tools:
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            out.append(f"{tool.get('name')}: inputSchema must be an object schema")
            continue
        out += problems(schema, tool.get("name", "?"))
    return out
