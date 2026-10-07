"""Green regression tests for the fixes from the red-team audit (problem.md).

Each test pins a fixed vulnerability or failure mode so it cannot come back.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core import controller
from core.controller import masked_env
from core.projects import discover_projects, load_project, load_projects_file
from core.settings import ConfigError, Settings, load_settings
from core.wrapper.codex import CodexWrapper


# -- P0-2: provider key written as OPENAI_API_KEY in .env reaches codex -------
def test_env_fallback_to_provider_key(settings):
    settings.env["OPENAI_API_KEY"] = "sk-direct"
    settings.env.pop("LLM_API_KEY", None)
    assert CodexWrapper(settings)._env()["OPENAI_API_KEY"] == "sk-direct"


def test_env_llm_api_key_wins(settings):
    settings.env["LLM_API_KEY"] = "sk-main"
    settings.env["OPENAI_API_KEY"] = "sk-direct"
    assert CodexWrapper(settings)._env()["OPENAI_API_KEY"] == "sk-main"


# -- P1-1: judge_error fails the run -------------------------------------------
def test_judge_error_fails_run(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import sys; sys.exit(3)", encoding="utf-8")
    monkeypatch.setattr(Settings, "python_for", lambda self, key=None: Path(sys.executable))
    rc = controller.main(["--root", str(repo), "run"])
    assert rc == 1
    run_dir = next((repo / "outputs").iterdir())
    result = json.loads((run_dir / "project_1" / "result.json").read_text())
    assert result["status"] == "completed" and result["judge"]["status"] == "judge_error"


# -- P1-3: masked_env -----------------------------------------------------------
def test_masked_env_case_insensitive_and_url_credentials():
    masked = masked_env({
        "openai_api_key": "sk-x",
        "LLM_API_PASSWORD": "pw",
        "MY_AUTH": "tok",
        "LLM_BASE_URL": "https://user:pass@proxy.example.com/v1",
        "LLM_MODEL": "gpt-5-codex",
    })
    assert masked["openai_api_key"] == "***"
    assert masked["LLM_API_PASSWORD"] == "***"
    assert masked["MY_AUTH"] == "***"
    assert masked["LLM_BASE_URL"] == "https://***@proxy.example.com/v1"
    assert masked["LLM_MODEL"] == "gpt-5-codex"


# -- P1-4: TOML injection rejected + self-check --------------------------------
def test_toml_injection_rejected(settings):
    settings.env["LLM_MODEL"] = 'gpt"\nexperimental_features = true\nx = "'
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        CodexWrapper(settings).config_toml()


def test_generated_toml_parses(settings):
    import tomllib
    data = tomllib.loads(CodexWrapper(settings).config_toml())
    assert data["model"] == "gpt-5-codex"
    assert data["mcp_servers"]["blender"]["url"].startswith("http")


# -- P2-1..6: config validation -------------------------------------------------
def test_config_top_level_must_be_object(repo: Path):
    (repo / "config.json").write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level"):
        load_settings(repo)


def test_projects_top_level_must_be_object(repo: Path):
    (repo / "projects.json").write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level"):
        load_projects_file(repo)


def test_limits_types_validated(repo: Path):
    data = json.loads((repo / "projects.json").read_text())
    data["projects"][0]["limits"] = {"min_rounds": "two"}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    p = discover_projects(s)[0]
    with pytest.raises(ConfigError, match="min_rounds"):
        p.resolved_limits(s.default_limits)


def test_limits_zero_or_negative_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text())
    data["projects"][0]["limits"] = {"max_turns": 0}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    p = discover_projects(s)[0]
    with pytest.raises(ConfigError, match="max_turns"):
        p.resolved_limits(s.default_limits)


def test_judge_path_traversal_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text())
    data["projects"][0]["judge"] = "../evil.py"
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="traversal"):
        discover_projects(load_settings(repo))


def test_enabled_must_be_bool(repo: Path):
    data = json.loads((repo / "projects.json").read_text())
    data["projects"][0]["enabled"] = "false"
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="enabled"):
        discover_projects(load_settings(repo))


def test_judge_timeout_must_be_positive_number(repo: Path):
    data = json.loads((repo / "projects.json").read_text())
    data["projects"][0]["judge_timeout"] = "soon"
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="judge_timeout"):
        discover_projects(load_settings(repo))


# -- P2-8: mcp_path sibling prefix ---------------------------------------------
def test_mcp_path_sibling_dir_not_translated(settings):
    settings.env["H3D_MCP_OUTPUTS_PREFIX"] = "/app/outputs"
    evil = settings.outputs_dir.resolve().parent / "outputs_evil" / "x.png"
    assert settings.mcp_path(evil) == str(evil.resolve())


# -- P2-7: bool score rejected ---------------------------------------------------
def test_bool_score_rejected(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        'import argparse, json\n'
        'ap = argparse.ArgumentParser()\n'
        'ap.add_argument("--workspace"); ap.add_argument("--run"); ap.add_argument("--out")\n'
        'a = ap.parse_args()\n'
        'json.dump({"score": True, "passed": True, "details": {}}, open(a.out, "w"))\n',
        encoding="utf-8")
    monkeypatch.setattr(Settings, "python_for", lambda self, key=None: Path(sys.executable))
    assert controller.main(["--root", str(repo), "run"]) == 1
    run_dir = next((repo / "outputs").iterdir())
    result = json.loads((run_dir / "project_1" / "result.json").read_text())
    assert result["judge"]["status"] == "judge_error"


# -- P2-9: compare defenses ------------------------------------------------------
def test_compare_rejects_rgb_reference(tmp_path: Path):
    from core.utils.compare import compare_dirs
    ref, cand = tmp_path / "ref", tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    for v in ("front",):
        Image.new("RGB", (16, 16), (0, 0, 0)).save(ref / f"{v}.png")
        Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(cand / f"{v}.png")
    with pytest.raises(ValueError, match="alpha"):
        compare_dirs(ref, cand, ("front",))


def test_compare_rejects_empty_reference(tmp_path: Path):
    from core.utils.compare import compare_dirs
    ref, cand = tmp_path / "ref", tmp_path / "cand"
    ref.mkdir(); cand.mkdir()
    for v in ("front",):
        Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(ref / f"{v}.png")
        Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(cand / f"{v}.png")
    with pytest.raises(ValueError, match="empty silhouette"):
        compare_dirs(ref, cand, ("front",))


# -- P2-12: prepare failure doesn't kill the run ---------------------------------
def test_prepare_failure_leaves_result(repo: Path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(controller.shutil, "copytree", boom)
    rc = controller.main(["--root", str(repo), "run"])
    assert rc == 1
    run_dir = next((repo / "outputs").iterdir())
    result = json.loads((run_dir / "project_1" / "result.json").read_text())
    assert result["status"] == "error" and "prepare failed" in result["error"]
