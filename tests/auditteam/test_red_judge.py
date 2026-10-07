"""Red-team round 2: key handling, scene failure path, judge robustness,
compare.py adversarial inputs, CLI rough edges."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core import controller
from core.settings import load_settings
from core.wrapper.codex import CodexWrapper, parse_codex_event

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# R22 (HIGH): the .env.example-documented setup (OPENAI_API_KEY= in .env) never
# reaches the codex subprocess — _env() only forwards LLM_API_KEY, and .env is
# never exported to os.environ. `run.py check` sees the key and stays silent.
def test_r22_dotenv_provider_key_never_reaches_codex(repo: Path, monkeypatch):
    (repo / ".env").write_text(
        "LLM_MODEL=gpt-5-codex\nOPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    s = load_settings(repo)
    assert s.env["OPENAI_API_KEY"] == "sk-from-dotenv"   # settings see it...
    env = CodexWrapper(s)._env()
    assert env.get("OPENAI_API_KEY") is None             # ...but codex does not


# R23: scene reset failure -> scene_error, judge is skipped (path untested by green tests)
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
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "scene_error"
    assert "judge" not in result      # judge skipped — documented, now proven
    assert rc == 1


# R24: the REAL project_1 judge handles a missing model.glb without Blender —
# this path is fully testable today but has no test (decision 17 skipped it)
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


# R25: missing deps key -> raw KeyError traceback from cmd_build, not ConfigError
def test_r25_build_missing_deps_key(repo: Path, monkeypatch):
    cfg = json.loads((repo / "config.json").read_text(encoding="utf-8"))
    del cfg["deps"]
    (repo / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(controller.Settings, "python_for",
                        lambda self, key=None: Path(sys.executable))
    with pytest.raises(KeyError):
        controller.main(["--root", str(repo), "build"])


# R26: --project naming a DISABLED project reports "unknown project" (misleading)
def test_r26_project_filter_on_disabled_project(repo: Path, monkeypatch):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["enabled"] = False
    data["projects"].append({"id": "project_2", "prompt": "x"})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    (repo / "projects" / "project_2" / "workspace").mkdir(parents=True)
    (repo / "projects" / "project_2" / "project_2.py").write_text("# j", encoding="utf-8")
    s = load_settings(repo)
    from core.projects import discover_projects
    from core.settings import ConfigError
    with pytest.raises(ConfigError, match="unknown project"):
        discover_projects(s, ["project_1"])  # it exists — it's just disabled


# R27: an error event without a message field dumps the WHOLE event (including
# any embedded base64 image) into audit.jsonl / result error strings
def test_r27_error_event_dumps_entire_payload():
    big_image = "A" * 100_000
    ev = {"type": "error", "item": {"type": "mcp_tool_call",
                                    "result": {"content": [{"type": "image",
                                                            "data": big_image}]}}}
    info = parse_codex_event(json.dumps(ev))
    assert len(info["error"]) > 50_000   # the whole event, image included


# R28: compare.load_mask trusts the alpha channel — an RGB render (no alpha)
# becomes an all-foreground mask, inflating IoU instead of failing
def test_r28_rgb_render_is_all_foreground(tmp_path: Path):
    from PIL import Image
    from core.utils.compare import load_mask, iou
    rgb = tmp_path / "rgb.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(rgb)   # pitch black, no alpha
    mask = load_mask(rgb)
    assert mask.all()                                  # everything "foreground"
    assert iou(mask, mask) == 1.0                      # garbage in -> perfect score


# R29: two empty renders score a perfect IoU (by design, but a silent pass
# if a render bug ever blanks both sides)
def test_r29_both_empty_is_perfect_score():
    from core.utils.compare import iou
    assert iou(np.zeros((8, 8), bool), np.zeros((8, 8), bool)) == 1.0


# R30: compare_dirs on size-mismatched renders raises (caught by project_1 ->
# score 0); proven here so the contract is explicit
def test_r30_size_mismatch_raises(tmp_path: Path):
    from PIL import Image
    from core.utils.compare import compare_dirs
    ref = tmp_path / "ref"; cand = tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    for v in ("front", "back", "left", "right", "top", "bottom"):
        Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(ref / f"{v}.png")
        Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(cand / f"{v}.png")
    with pytest.raises(ValueError):
        compare_dirs(ref, cand, ("front", "back", "left", "right", "top", "bottom"))
