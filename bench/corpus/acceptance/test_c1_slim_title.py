"""Acceptance c1: server._slim must keep a real property named "title"."""

from subtask import server


def test_property_named_title_is_kept():
    schema = {
        "title": "Args",
        "type": "object",
        "properties": {"title": {"title": "Title", "type": "string"}, "n": {"title": "N", "type": "integer"}},
        "required": ["title"],
    }
    out = server._slim(schema)
    assert "title" not in out
    assert out["properties"]["title"] == {"type": "string"}
    assert out["properties"]["n"] == {"type": "integer"}


def test_step_tool_publishes_title():
    tool = next(t for t in server.mcp._tool_manager.list_tools() if t.name == "step")
    assert tool.parameters["properties"]["title"] == {"type": "string"}
    assert "title" in tool.parameters["required"]
