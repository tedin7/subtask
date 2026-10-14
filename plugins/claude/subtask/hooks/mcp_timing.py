"""PostToolUse hook: print `mcp <server>/<tool> <duration> ok|error` as a user-visible systemMessage.

For subtask's own `step` tool it shows the announcement itself: Claude Code folds MCP results
under "Called ...", so without this the user never sees the explanation the agent asked for.

Reads the hook input on stdin, stdlib only, never fails the tool call (always exits 0).
"""

import json
import sys


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    name = str(data.get("tool_name") or "")
    if not name.startswith("mcp__"):
        return
    _, server, tool = (name.split("__", 2) + ["", ""])[:3]
    ms = data.get("duration_ms")
    took = f"{ms / 1000:.1f}s" if isinstance(ms, (int, float)) else "?s"
    resp = data.get("tool_response")
    failed = data.get("hook_event_name") == "PostToolUseFailure" or (
        isinstance(resp, dict) and resp.get("isError") is True
    )
    line = f"mcp {server}/{tool} {took} {'error' if failed else 'ok'}"
    if tool == "step" and "subtask" in server and not failed:
        text = _text(resp).strip()
        if text:
            line = f"{text}\n({line})"
    print(json.dumps({"systemMessage": line}))


def _text(resp: object) -> str:
    """The text of an MCP tool result, whatever shape the client hands over."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        resp = resp.get("content", resp.get("result", ""))
        if isinstance(resp, str):
            return resp
    if isinstance(resp, list):
        return "\n".join(b.get("text", "") for b in resp if isinstance(b, dict))
    return ""


if __name__ == "__main__":
    main()
    sys.exit(0)
