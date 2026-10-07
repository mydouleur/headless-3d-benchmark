"""Green-path tests for the controller. Serial by design (no xdist).

A fake codex binary (a python script) stands in for the real CLI; scene
management is disabled via config.json; judges are tiny scripts that write a
score.json. No network, no Docker, no real Blender.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.settings import load_settings  # noqa: E402

FAKE_CODEX = r"""
import json, os, sys
from pathlib import Path

behavior = os.environ.get("FAKE_CODEX_BEHAVIOR", "ok")
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")

if behavior == "context_limit":
    print("stream error: maximum context length exceeded", file=sys.stderr)
    sys.exit(1)
if behavior == "fail":
    print("boom", file=sys.stderr)
    sys.exit(1)

print(json.dumps({"type": "item.completed",
                  "item": {"type": "reasoning", "text": "I should model a mug body first."}}))
print(json.dumps({"type": "item.completed",
                  "item": {"type": "mcp_tool_call", "server": "blender",
                           "tool": "execute_blender_code", "arguments": {"code": "..."},
                           "status": "completed"}}))
print(json.dumps({"type": "item.completed",
                  "item": {"type": "mcp_tool_call", "server": "blender",
                           "tool": "get_viewport_screenshot", "arguments": {},
                           "status": "completed",
                           "result": {"content": [{"type": "image", "mimeType": "image/png",
                                                   "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}]}}}))
print(json.dumps({"type": "item.completed",
                  "item": {"type": "command_execution", "command": "ls refs/",
                           "exit_code": 0, "aggregated_output": "front.png"}}))
print(json.dumps({"type": "item.completed",
                  "item": {"type": "file_change", "changes": [{"path": "model.glb", "kind": "add"}]}}))
Path("model.glb").write_bytes(b"GLB")
print(json.dumps({"type": "item.completed",
                  "item": {"type": "agent_message", "text": "done, mug exported"}}))
tokens = 1000 if behavior == "many_tokens" else 100
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": tokens, "output_tokens": 5, "cached_input_tokens": 3}}))
sys.exit(0)
"""

FAKE_JUDGE = r"""
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("--workspace"); ap.add_argument("--run"); ap.add_argument("--out")
args = ap.parse_args()
with open(args.out, "w", encoding="utf-8") as fh:
    json.dump({"score": 88.0, "passed": True, "details": {"fake": True}}, fh)
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A minimal benchmark checkout in tmp_path.

    Isolated from the host/container environment: LLM_* variables from a real
    deployment must not leak into the fake-CLI tests, and privilege dropping
    (codex.user) is off outside the docker image.
    """
    for var in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_ENV_KEY", "LLM_WIRE_API",
                "LLM_MODEL", "MCP_URL", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                "ANTHROPIC_API_KEY", "H3D_MCP_OUTPUTS_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
    (tmp_path / "projects" / "project_1" / "workspace").mkdir(parents=True)
    (tmp_path / "projects" / "project_1" / "workspace" / "hint.txt").write_text("refs here")
    (tmp_path / "projects.json").write_text(json.dumps({
        "projects": [{"id": "project_1", "prompt": "Make a mug."}],
    }), encoding="utf-8")
    (tmp_path / "projects" / "project_1" / "project_1.py").write_text(FAKE_JUDGE, encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "benchmark": {"name": "t", "version": "0.1.0"},
        "deps": {"blender": "5.2.2", "blendermcp": "2.1.3", "codexcli": "0.160.0"},
        # fake codex: python <fake_codex.py> ... (extra_args go before `exec`)
        "codex": {"binary": sys.executable, "extra_args": [str(fake_codex)], "user": ""},
        "scene": {"reset": False, "save_blend": False},
        "defaults": {"limits": {"min_rounds": 1, "max_turns": 10, "max_tokens": None}},
    }), encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_MODEL=gpt-5-codex\nLLM_API_KEY=sk-test\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(repo: Path):
    return load_settings(repo)
