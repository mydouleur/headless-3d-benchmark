"""Red-team: settings/config/projects adversarial inputs.

auditteam convention: assert the DESIRED behavior; a failure is an open
finding for the dev team. R01-R08 were exploited in the first audit round
(problem.md) and flipped to security-regression assertions after T17.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.projects import discover_projects, load_project, load_projects_file
from core.settings import ConfigError, load_settings
from core.controller import masked_env
from core.wrapper.codex import CodexWrapper


# R01: secrets must be masked regardless of key case / nonstandard names,
# and credentials embedded in URLs must be redacted (fixed in T17)
def test_r01_masked_env_covers_case_and_url_credentials():
    env = {"OPENAI_API_KEY": "sk-1", "openai_api_key": "sk-2",
           "LLM_API_PASSWORD": "pw-3", "MY_AUTH": "tok-4",
           "LLM_BASE_URL": "https://user:pass@proxy.example.com/v1",
           "LLM_MODEL": "m"}
    masked = masked_env(env)
    assert masked["OPENAI_API_KEY"] == "***"
    assert masked["openai_api_key"] == "***"
    assert masked["LLM_API_PASSWORD"] == "***"
    assert masked["MY_AUTH"] == "***"
    assert masked["LLM_BASE_URL"] == "https://***@proxy.example.com/v1"
    assert masked["LLM_MODEL"] == "m"


# R02: TOML injection via .env values must be rejected as a ConfigError,
# and the generated config must always parse (fixed in T17)
def test_r02_config_toml_injection_rejected(settings):
    settings.env["LLM_MODEL"] = 'gpt-5"\nexperimental_features = true\n# '
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        CodexWrapper(settings).config_toml()


# R02b (NEW, T17 residual): the [model_providers.<provider>] section header
# interpolates LLM_PROVIDER without validation — a dotted provider silently
# creates a nested table (config codex won't match), a "]" breaks TOML and
# escapes as a raw TOMLDecodeError instead of ConfigError
def test_r02b_provider_name_must_be_validated(settings):
    settings.env["LLM_PROVIDER"] = "open.router"   # dotted: valid TOML, wrong shape
    import tomllib
    parsed = tomllib.loads(CodexWrapper(settings).config_toml())
    assert list(parsed["model_providers"]) == ["open.router"], \
        "dotted provider silently became a nested table"
    settings.env["LLM_PROVIDER"] = "bad]"          # breaks the section header
    with pytest.raises(ConfigError):               # must be ConfigError, not TOMLDecodeError
        CodexWrapper(settings).config_toml()


# R03/R04: malformed top-level JSON must be a clean ConfigError (fixed in T17)
def test_r03_projects_json_top_level_list_rejected(repo: Path):
    (repo / "projects.json").write_text(json.dumps(["project_1"]), encoding="utf-8")
    with pytest.raises(ConfigError, match="top level"):
        load_projects_file(repo)


def test_r04_config_json_top_level_list_rejected(repo: Path):
    (repo / "config.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ConfigError, match="top level"):
        load_settings(repo)


# R05/R06: limits must be type- and range-checked as ConfigError (fixed in T17)
def test_r05_min_rounds_wrong_type_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["limits"] = {"min_rounds": "two", "max_turns": 5}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    p = discover_projects(s)[0]
    with pytest.raises(ConfigError, match="min_rounds"):
        p.resolved_limits(s.default_limits)


def test_r06_negative_max_turns_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["limits"] = {"max_turns": -3}
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    p = discover_projects(s)[0]
    with pytest.raises(ConfigError, match="max_turns"):
        p.resolved_limits(s.default_limits)


# R07: judge path traversal must be rejected (fixed in T17)
def test_r07_judge_path_traversal_rejected(repo: Path):
    (repo / "projects" / "evil.py").write_text("# not a judge", encoding="utf-8")
    entry = {"id": "project_1", "prompt": "x", "judge": "../evil.py"}
    with pytest.raises(ConfigError, match="traversal"):
        load_project(repo / "projects", entry)


# R08: enabled must be a real bool (fixed in T17)
def test_r08_enabled_string_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["enabled"] = "false"
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="enabled"):
        discover_projects(load_settings(repo))


# R09: judge_timeout must be a positive number (fixed in T17)
def test_r09_judge_timeout_wrong_type_rejected(repo: Path):
    entry = {"id": "project_1", "prompt": "x", "judge_timeout": "soon"}
    with pytest.raises(ConfigError, match="judge_timeout"):
        load_project(repo / "projects", entry)


# R33 (NEW in T17): a DISABLED project is fully validated (dir/judge/workspace
# must exist) — a work-in-progress disabled entry now aborts the whole run,
# contradicting docs/projects_ref.md "false 时跳过该题,不用删条目"
def test_r33_disabled_project_should_not_need_valid_files(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"].append({"id": "project_2", "enabled": False, "prompt": "wip"})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    # project_2 has no directory at all; disabled => should be skipped silently
    s = load_settings(repo)
    assert [p.id for p in discover_projects(s)] == ["project_1"]
