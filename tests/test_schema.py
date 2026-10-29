"""R01/R02: published schemas are semantically valid; one version everywhere."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from subtask import schemacheck

ROOT = Path(__file__).resolve().parents[1]


def _tools() -> list[dict]:
    return json.loads((ROOT / "subtask" / "tools.json").read_text())["tools"]


def test_published_tools_have_no_semantic_defects():
    assert schemacheck.tool_problems(_tools()) == []


def test_start_accepts_model_on_every_entry_point():
    """T-1685: `model` was dropped by the MCP start tool and rejected by the fast server."""
    import inspect

    from subtask import core, fastserver, server

    start_tool = next(t for t in _tools() if t["name"] == "start")
    assert "model" in inspect.signature(server.start).parameters
    assert "model" in start_tool["inputSchema"]["properties"]
    assert fastserver._start_args({"task": "x", "model": "opencode-go/qwen3.8-flash"})["model"]
    declared = set(inspect.signature(server.start).parameters)
    assert declared <= fastserver.START_ARGS
    assert fastserver.START_ARGS <= set(inspect.signature(core.start).parameters)


def test_server_start_forwards_model_to_core(monkeypatch):
    from subtask import core, server

    seen = {}
    monkeypatch.setattr(core, "start", lambda *a, **k: seen.update(k) or "ok")
    server.start("t", model="m-1")
    assert seen["model"] == "m-1"


def test_tool_descriptions_are_the_same_on_every_python():
    assert all("\n    " not in (t.get("description") or "") for t in _tools())


def test_slim_keeps_a_property_named_title_but_drops_annotations():
    from subtask.server import _slim

    schema = {
        "title": "toolArguments",
        "type": "object",
        "properties": {
            "title": {"title": "Title", "type": "string"},
            "opt": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Opt"},
        },
        "required": ["title"],
    }
    assert _slim(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "opt": {"type": "string"}},
        "required": ["title"],
    }


@pytest.mark.parametrize(
    "schema, expected",
    [
        ({"type": "object", "properties": {}, "required": ["x"]}, "required 'x' not declared"),
        ({"type": "object", "properties": {"x": {"type": "str"}}}, "unknown type 'str'"),
        ({"type": "object", "properties": {"x": {"type": "integer", "default": "a"}}}, "default 'a'"),
        ({"type": "object", "properties": {"x": {"$ref": "#/$defs/nope"}}}, "unresolved $ref"),
        ({"type": "object", "properties": {"x": {"$ref": "https://evil/x"}}}, "non-local $ref"),
    ],
)
def test_checker_finds_defects(schema, expected):
    assert any(expected in p for p in schemacheck.problems(schema))


def test_checker_follows_ref_and_allof():
    schema = {
        "$defs": {"base": {"type": "object", "properties": {"a": {"type": "string"}}}},
        "type": "object",
        "allOf": [{"$ref": "#/$defs/base"}],
        "properties": {"b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    assert schemacheck.problems(schema) == []


def test_checker_does_not_judge_pattern_properties():
    schema = {"type": "object", "patternProperties": {"^x": {}}, "required": ["xa"]}
    assert schemacheck.problems(schema) == []


def test_tools_json_is_generated_from_the_canonical_source():
    from subtask import server

    assert server.dump_tools() == json.loads((ROOT / "subtask" / "tools.json").read_text())


def test_one_version_for_package_cli_and_mcp():
    import subtask
    from subtask import fastserver

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "version" in project.get("dynamic", [])
    assert fastserver.SERVER_INFO["version"] == subtask.__version__
    assert subtask.__version__.count(".") == 2


def test_server_instructions_teach_the_cheap_pattern_without_the_skill():
    from subtask import fastserver

    text = fastserver.SPEC["instructions"]
    assert "ONE start_many" in text and "ONE wait" in text and "Never poll" in text
    assert "Codex 0.164.0" in text and "timeout=60" in text and "cursor=" in text
    assert len(text) < 2000  # sent to every client session: the 0.4 playbook, about 400 tokens


def test_fast_server_start_args_follow_the_start_signature():
    """T-1813: START_ARGS was a hand-written list that drifted from server.start."""
    import inspect

    from subtask import fastserver, server

    declared = set(inspect.signature(server.start).parameters)
    assert set(fastserver.START_ARGS) == declared
    published = next(t for t in _tools() if t["name"] == "start")["inputSchema"]["properties"]
    assert set(published) == declared
