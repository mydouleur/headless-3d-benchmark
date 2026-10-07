"""Settings and codex config generation (green paths)."""
from __future__ import annotations

from pathlib import Path

from core.settings import load_settings
from core.wrapper.codex import CodexWrapper


def test_load_settings_reads_project_json_and_env(repo: Path):
    s = load_settings(repo)
    assert s.benchmark_name == "t" and s.benchmark_version == "0.1.0"
    assert s.deps["blender"] == "5.2.2"
    assert s.model == "gpt-5-codex"
    assert s.env["LLM_API_KEY"] == "sk-test"
    assert s.scene_reset is False
    assert s.default_limits["max_turns"] == 10


def test_real_environment_overrides_dotenv(repo: Path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "other-model")
    assert load_settings(repo).model == "other-model"


def test_codex_config_default_openai_has_no_provider_section(settings):
    toml = CodexWrapper(settings).config_toml()
    assert 'model = "gpt-5-codex"' in toml
    assert 'model_provider = "openai"' in toml
    assert "[model_providers." not in toml  # built-in provider reads OPENAI_API_KEY
    assert '[mcp_servers.blender]' in toml and 'url = "http://blender:8000/mcp"' in toml


def test_codex_config_third_party_provider(settings, monkeypatch):
    settings.env["LLM_PROVIDER"] = "openrouter"
    settings.env["LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
    settings.env["LLM_ENV_KEY"] = "OPENROUTER_API_KEY"
    toml = CodexWrapper(settings).config_toml()
    assert "[model_providers.openrouter]" in toml
    assert 'base_url = "https://openrouter.ai/api/v1"' in toml
    assert 'env_key = "OPENROUTER_API_KEY"' in toml
    assert 'wire_api = "chat"' in toml  # chat is the default for non-openai


def test_codex_config_written_to_codex_home(settings, tmp_path: Path):
    CodexWrapper(settings).write_config()  # CODEX_HOME is redirected by the repo fixture
    written = (tmp_path / "codex-home" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5-codex"' in written


def test_mcp_path_passthrough_and_prefix(settings, monkeypatch):
    p = (settings.outputs_dir / "run" / "project_1" / "scene.blend")
    assert settings.mcp_path(p) == str(p.resolve())
    monkeypatch.setenv("H3D_MCP_OUTPUTS_PREFIX", "/app/outputs")
    settings.env["H3D_MCP_OUTPUTS_PREFIX"] = "/app/outputs"
    translated = settings.mcp_path(p)
    assert translated.startswith("/app/outputs/") and translated.endswith("scene.blend")
