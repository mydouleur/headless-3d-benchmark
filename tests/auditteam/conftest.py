"""Red-team / adversarial fixtures. Self-contained (does not import tests/).

Same idea as tests/conftest.py (fake codex + fake judge, no network/Docker),
but each red test mutates the checkout or the event stream adversarially.
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

if behavior == "context_limit":
    print("stream error: maximum context length exceeded", file=sys.stderr)
    sys.exit(1)
if behavior == "fail":
    print("boom", file=sys.stderr)
    sys.exit(1)
if behavior == "bad_usage":
    # adversarial: usage fields are non-numeric strings
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": "many", "output_tokens": 5}}))
    sys.exit(0)
if behavior == "bad_image":
    # adversarial: image block with invalid base64 payload
    print(json.dumps({"type": "item.completed",
                      "item": {"type": "mcp_tool_call", "server": "blender",
                               "tool": "get_viewport_screenshot",
                               "result": {"content": [{"type": "image",
                                                       "mimeType": "image/png",
                                                       "data": "!!!not-base64!!!"}]}}}))
    sys.exit(0)
if behavior == "slow_after_bad_usage":
    # print a poisonous event, then keep running so we can see whether the
    # controller reaps us when event processing blows up
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": "many"}}), flush=True)
    import time
    time.sleep(30)
    sys.exit(0)

print(json.dumps({"type": "item.completed",
                  "item": {"type": "agent_message", "text": "done"}}))
print(json.dumps({"type": "turn.completed",
                  "usage": {"input_tokens": 100, "output_tokens": 5}}))
Path("model.glb").write_bytes(b"GLB")
sys.exit(0)
"""

FAKE_JUDGE = r"""
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("--workspace"); ap.add_argument("--run"); ap.add_argument("--out")
args = ap.parse_args()
with open(args.out, "w", encoding="utf-8") as fh:
    json.dump({"score": 88.0, "passed": True, "details": {}}, fh)
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
    (tmp_path / "projects" / "project_1" / "workspace").mkdir(parents=True)
    (tmp_path / "projects.json").write_text(json.dumps({
        "projects": [{"id": "project_1", "prompt": "Make a mug."}],
    }), encoding="utf-8")
    (tmp_path / "projects" / "project_1" / "project_1.py").write_text(FAKE_JUDGE, encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "benchmark": {"name": "t", "version": "0.1.0"},
        "deps": {"blender": "5.2.2", "blendermcp": "2.1.3", "codexcli": "0.160.0"},
        "codex": {"binary": sys.executable, "extra_args": [str(fake_codex)]},
        "scene": {"reset": False, "save_blend": False},
        "defaults": {"limits": {"min_rounds": 1, "max_turns": 10, "max_tokens": None}},
    }), encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_MODEL=gpt-5-codex\nLLM_API_KEY=sk-test\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(repo: Path):
    return load_settings(repo)
