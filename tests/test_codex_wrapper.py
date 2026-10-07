"""Codex event parsing (green paths)."""
from __future__ import annotations

import json

from core.wrapper.codex import parse_codex_event


def test_parse_usage():
    line = json.dumps({"type": "turn.completed",
                       "usage": {"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 3}})
    assert parse_codex_event(line)["usage"] == {"input": 100, "output": 5, "cached": 3}


def test_parse_mcp_call():
    line = json.dumps({"type": "item.completed",
                       "item": {"type": "mcp_tool_call", "server": "blender",
                                "tool": "execute_blender_code", "arguments": {"code": "x"},
                                "status": "completed"}})
    call = parse_codex_event(line)["mcp_call"]
    assert call["server"] == "blender" and call["tool"] == "execute_blender_code"
    assert call["status"] == "completed"


def test_parse_error_and_agent_text():
    err = parse_codex_event(json.dumps({"type": "error", "message": "boom"}))
    assert err["error"] == "boom"
    msg = parse_codex_event(json.dumps({"type": "item.completed",
                                        "item": {"type": "agent_message", "text": "done"}}))
    assert msg["agent_text"] == "done"


def test_parse_garbage_is_ignored():
    assert parse_codex_event("not json") == {}
    assert parse_codex_event(json.dumps({"type": "something_new"})) == {}
    assert parse_codex_event(json.dumps([1, 2])) == {}
