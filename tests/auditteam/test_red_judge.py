"""Red-team: key handling, scene failure path, judge robustness, compare.py.

auditteam convention: assert the DESIRED behavior; a failure is an open
finding for the dev team. R22-R30 were exploited/probed in the first audit
round (problem.md) and flipped where T17 fixed them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core import controller
from core.settings import ConfigError, load_settings
from core.wrapper.codex import CodexWrapper, parse_codex_event

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# R22: the .env.example-documented setup (provider key directly in .env) must
# reach the codex subprocess even without LLM_API_KEY (fixed in T17)
def test_r22_dotenv_provider_key_reaches_codex(repo: Path, monkeypatch):
    (repo / ".env").write_text(
        "LLM_MODEL=gpt-5-codex\nOPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    s = load_settings(repo)
    assert CodexWrapper(s)._env()["OPENAI_API_KEY"] == "sk-from-dotenv"


# R23: scene reset failure -> scene_error, judge skipped, result written (pinned)
def test_r23_scene_reset_failure_skips_judge(repo: Path, monkeypatch):
    cfg = json.loads((repo / "config.json").read_text(encoding="utf-8"))
    cfg["scene"] = {"reset": True, "save_blend": True}
    (repo / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    import core.wrapper.blender_mcp as bm

    async def fake_exec(url, code, timeout=300):
        return "MCP unreachable mock", True

    monkeypatch.setattr(bm, "execute_blender_code", fake_exec)
    monkeypatch.setattr(controller.Settings, "python_for",
                        lambda self, key=None: Path(sys.executable))
    rc = controller.main(["--root", str(repo), "run"])
    run_dir = next((repo / "outputs").iterdir())
    result = json.loads((run_dir / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "scene_error"
    assert "judge" not in result
    assert rc == 1


# R24: the REAL project_1 judge handles a missing model.glb without Blender —
# pinned as a regression test (needs numpy/Pillow only)
def test_r24_real_project1_judge_missing_model(tmp_path: Path):
    out = tmp_path / "score.json"
    ws = tmp_path / "ws"; ws.mkdir()
    run_dir = tmp_path / "run"; run_dir.mkdir()
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "projects" / "project_1" / "project_1.py"),
         "--workspace", str(ws), "--run", str(run_dir), "--out", str(out)],
        env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    score = json.loads(out.read_text(encoding="utf-8"))
    assert score["score"] == 0.0 and score["passed"] is False
    assert "model.glb not found" in score["details"]["error"]


# R25: missing deps key must be a clean ConfigError, not a KeyError traceback
def test_r25_build_missing_deps_key(repo: Path, monkeypatch):
    cfg = json.loads((repo / "config.json").read_text(encoding="utf-8"))
    del cfg["deps"]
    (repo / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ConfigError, match="deps"):
        load_settings(repo)


# R26: --project naming a disabled project must say "disabled", not "unknown"
def test_r26_project_filter_on_disabled_project(repo: Path):
    from core.projects import discover_projects
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["enabled"] = False
    data["projects"].append({"id": "project_2", "prompt": "x"})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    (repo / "projects" / "project_2" / "workspace").mkdir(parents=True)
    (repo / "projects" / "project_2" / "project_2.py").write_text("# j", encoding="utf-8")
    s = load_settings(repo)
    with pytest.raises(ConfigError, match="disabled"):
        discover_projects(s, ["project_1"])
    with pytest.raises(ConfigError, match="unknown"):
        discover_projects(s, ["project_9"])


# R27: an error event without a message must NOT dump the whole event
# (base64 screenshots included) into audit/result strings (fixed in T17)
def test_r27_error_event_is_clipped():
    big_image = "A" * 100_000
    ev = {"type": "error", "item": {"type": "mcp_tool_call",
                                    "result": {"content": [{"type": "image",
                                                            "data": big_image}]}}}
    info = parse_codex_event(json.dumps(ev))
    assert len(info["error"]) <= 2100
    assert big_image not in info["error"]


# R28/R29: raw compare primitives keep documented semantics; the DEFENSE lives
# at the compare_dirs call site (reference requires alpha + non-empty mask)
def test_r28_rgb_reference_rejected(tmp_path: Path):
    from PIL import Image
    from core.utils.compare import compare_dirs
    ref = tmp_path / "ref"; cand = tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    Image.new("RGB", (16, 16), (0, 0, 0)).save(ref / "front.png")
    Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(cand / "front.png")
    with pytest.raises(ValueError, match="alpha"):
        compare_dirs(ref, cand, ("front",))


def test_r29_empty_reference_rejected(tmp_path: Path):
    from PIL import Image
    from core.utils.compare import compare_dirs
    ref = tmp_path / "ref"; cand = tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(ref / "front.png")
    Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(cand / "front.png")
    with pytest.raises(ValueError, match="empty silhouette"):
        compare_dirs(ref, cand, ("front",))


# R30: size-mismatched renders raise (project_1 catches -> score 0); pinned
def test_r30_size_mismatch_raises(tmp_path: Path):
    from PIL import Image
    from core.utils.compare import compare_dirs
    ref = tmp_path / "ref"; cand = tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(ref / "front.png")
    Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(cand / "front.png")
    with pytest.raises(ValueError):
        compare_dirs(ref, cand, ("front",))
