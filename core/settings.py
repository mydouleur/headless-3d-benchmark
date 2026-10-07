"""Settings: project.json + .env loading. Single source of truth for config."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Invalid project.json / .env / project task configuration."""


DEFAULT_LIMITS = {"min_rounds": 1, "max_turns": 10, "max_tokens": None}

DEFAULT_NAG_PROMPT = (
    "还没有达到要求。请检查你当前的模型：和参考输入逐项对比形状、比例和部件位置，"
    "继续改进；确认模型仍然导出在同一个 glb 路径。完成后回复一段简短总结。")

ENV_KEYS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_ENV_KEY", "LLM_WIRE_API",
            "LLM_MODEL", "MCP_URL", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


def load_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines; real environment variables win over the file."""
    data: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    merged = dict(data)
    merged.update({k: v for k, v in os.environ.items() if k in data or k in ENV_KEYS})
    return merged


@dataclass
class Settings:
    root: Path
    benchmark_name: str
    benchmark_version: str
    deps: dict[str, str]
    python_default: str
    python_envs: dict[str, str]
    codex_binary: str
    codex_sandbox: str
    codex_extra_args: list[str]
    scene_reset: bool
    scene_save_blend: bool
    nag_prompt: str
    default_limits: dict[str, Any]
    projects_dir: Path
    outputs_dir: Path
    env: dict[str, str]

    @property
    def model(self) -> str:
        return self.env.get("LLM_MODEL", "")

    @property
    def mcp_url(self) -> str:
        return self.env.get("MCP_URL", "http://blender:8000/mcp")

    def python_for(self, key: str | None) -> Path:
        key = key or self.python_default
        if key not in self.python_envs:
            raise ConfigError(f"unknown python env {key!r}; available: {sorted(self.python_envs)}")
        venv = self.root / self.python_envs[key]
        exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not exe.is_file():
            raise ConfigError(f"python env {key} not installed at {venv}; run: python3 envinstall.py")
        return exe

    def mcp_path(self, path: Path) -> str:
        """How the BlenderMCP server sees a local path. In compose both
        containers mount ./outputs at /app/outputs, so paths are identical;
        on a host setup set H3D_MCP_OUTPUTS_PREFIX to the server's mount point."""
        prefix = self.env.get("H3D_MCP_OUTPUTS_PREFIX")
        p = str(path.resolve())
        if prefix:
            base = str(self.outputs_dir.resolve())
            if p.startswith(base):
                return prefix.rstrip("/") + p[len(base):].replace("\\", "/")
        return p


def load_settings(root: Path) -> Settings:
    pj_path = root / "project.json"
    if not pj_path.is_file():
        raise ConfigError(f"project.json not found in {root}")
    try:
        pj = json.loads(pj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"project.json is not valid JSON: {exc}") from exc
    try:
        bench = pj["benchmark"]
        codex = pj.get("codex", {})
        scene = pj.get("scene", {})
        python = pj.get("python", {})
        return Settings(
            root=root,
            benchmark_name=bench["name"],
            benchmark_version=bench["version"],
            deps=pj.get("deps", {}),
            python_default=python.get("default", "3.12"),
            python_envs=python.get("envs", {"3.12": ".venv/py312", "3.14": ".venv/py314"}),
            codex_binary=codex.get("binary", "codex"),
            codex_sandbox=codex.get("sandbox", "workspace-write"),
            codex_extra_args=list(codex.get("extra_args", [])),
            scene_reset=bool(scene.get("reset", True)),
            scene_save_blend=bool(scene.get("save_blend", True)),
            nag_prompt=pj.get("defaults", {}).get("nag_prompt") or DEFAULT_NAG_PROMPT,
            default_limits={**DEFAULT_LIMITS, **pj.get("defaults", {}).get("limits", {})},
            projects_dir=root / pj.get("projects_dir", "projects"),
            outputs_dir=root / pj.get("outputs_dir", "outputs"),
            env=load_dotenv(root / ".env"),
        )
    except KeyError as exc:
        raise ConfigError(f"project.json missing key: {exc}") from exc
