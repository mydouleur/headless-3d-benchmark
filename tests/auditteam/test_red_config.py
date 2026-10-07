"""Red-team: settings/config/projects adversarial inputs.

Each test PROVES a current behavior; names say what the behavior is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.projects import discover_projects, load_project, load_projects_file
from core.settings import ConfigError, load_settings
from core.controller import masked_env
from core.wrapper.codex import CodexWrapper


# R01: lowercase secret key in .env is NOT masked in the run.json env snapshot
def test_r01_masked_env_misses_lowercase_and_nonstandard_keys():
    env = {"OPENAI_API_KEY": "sk-1", "openai_api_key": "sk-2",
           "LLM_API_PASSWORD": "pw-3", "MY_AUTH": "tok-4", "LLM_MODEL": "m"}
    masked = masked_env(env)
    assert masked["OPENAI_API_KEY"] == "***"          # masked (uppercase KEY)
    assert masked["openai_api_key"] == "sk-2"          # LEAK: case-sensitive regex
    assert masked["LLM_API_PASSWORD"] == "pw-3"        # LEAK: PASSWORD not in pattern
    assert masked["MY_AUTH"] == "tok-4"                # LEAK: AUTH not in pattern


# R02: TOML injection into generated codex config via .env values
def test_r02_config_toml_injection_via_model_name(settings):
    settings.env["LLM_MODEL"] = 'gpt-5"\nexperimental_features = true\n# '
    toml = CodexWrapper(settings).config_toml()
    import tomllib
    parsed = tomllib.loads(toml)  # parses cleanly: the injection SUCCEEDS
    assert parsed["experimental_features"] is True   # attacker-injected key is live
    assert parsed["model"] == "gpt-5"                # original value hijacked


# R03: projects.json top-level non-dict crashes with AttributeError, not ConfigError
def test_r03_projects_json_top_level_list_crashes(repo: Path):
    (repo / "projects.json").write_text(json.dumps(["project_1"]), encoding="utf-8")
    with pytest.raises(AttributeError):  # not ConfigError -> ugly crash, exit != 2
        load_projects_file(repo)


# R04: config.json top-level non-dict crashes with TypeError, not ConfigError
def test_r04_config_json_top_level_list_crashes(repo: Path):
    (repo / "config.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(TypeError):
        load_settings(repo)


# R05: min_rounds of wrong type crashes with TypeError, not ConfigError
def test_r05_min_rounds_wrong_type_crashes(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["limits"] = {"min_rounds": "two", "max_turns": 5}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    p = discover_projects(s)[0]
    with pytest.raises(TypeError):  # "'<' not supported between 'str' and 'int'"
        p.resolved_limits(s.default_limits)


# R06: negative / zero max_turns is accepted -> task ends with 0 rounds, still judged
def test_r06_negative_max_turns_accepted(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["limits"] = {"max_turns": -3}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    limits = discover_projects(s)[0].resolved_limits(s.default_limits)
    assert limits["max_turns"] == -3  # no validation; loop ends before round 1


# R07: judge path traversal: judge filename is not confined to the project dir
def test_r07_judge_path_traversal(repo: Path):
    evil = repo / "projects" / "evil.py"
    evil.write_text("# not a judge", encoding="utf-8")
    entry = {"id": "project_1", "prompt": "x", "judge": "../evil.py"}
    p = load_project(repo / "projects", entry)  # accepted without complaint
    assert p.judge == repo / "projects" / "project_1" / ".." / "evil.py"
    assert not str(p.judge.resolve()).startswith(str((repo / "projects" / "project_1").resolve()))


# R08: enabled:"false" (string) silently KEEPS the project enabled
def test_r08_enabled_string_is_truthy(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["enabled"] = "false"
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    assert [p.id for p in discover_projects(s)] == ["project_1"]  # still enabled!


# R09: judge_timeout of wrong type crashes with ValueError, not ConfigError
def test_r09_judge_timeout_wrong_type(repo: Path):
    entry = {"id": "project_1", "prompt": "x", "judge_timeout": "soon"}
    with pytest.raises(ValueError):
        load_project(repo / "projects", entry)
