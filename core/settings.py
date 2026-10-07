"""Settings: config.json + .env loading. Single source of truth for config."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Invalid config.json / .env / projects.json configuration."""


DEFAULT_LIMITS = {"min_rounds": 1, "max_turns": 10, "max_tokens": None}

DEFAULT_NAG_PROMPT = (
    "还没有达到要求。请检查你当前的模型：和参考输入逐项对比形状、比例和部件位置，"
    "继续改进；确认模型仍然导出在同一个 glb 路径。完成后回复一段简短总结。")

DEFAULT_BASE_PROMPT = """\
你正在一个自动化的 3D 建模 benchmark 中工作，全程无头运行，没有人会回答你的问题：自己决策，完成任务前不要提问。

规则：
- 通过 Blender MCP 的工具在 Blender 中建模。
- 最终模型导出为当前工作目录（workspace）下的 model.glb；只有这个文件会被评分，改进时覆盖它。
- 坐标约定：+Y 向上，物体正面朝 +Z（Blender 里 +Z 向上、正面朝 -Y，glTF 导出器会自动转换）。
- 可以用 get_viewport_screenshot 截图自查，对照参考输入改进后再导出。
- 完成后回复一段简短总结，然后停止调用工具。"""

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
    codex_user: str  # run the agent CLI as this uid name ("" = current user)
    scene_reset: bool
    scene_save_blend: bool
    nag_prompt: str
    base_prompt: str
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
        p = path.resolve()
        if prefix:
            base = self.outputs_dir.resolve()
            if p.is_relative_to(base):
                rel = p.relative_to(base).as_posix()
                return prefix.rstrip("/") + ("/" + rel if rel != "." else "")
        return str(p)


def load_settings(root: Path) -> Settings:
    pj_path = root / "config.json"
    if not pj_path.is_file():
        raise ConfigError(f"config.json not found in {root}")
    try:
        pj = json.loads(pj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON: {exc}") from exc
    if not isinstance(pj, dict):
        raise ConfigError("config.json: top level must be an object")
    try:
        bench = pj["benchmark"]
        codex = pj.get("codex", {})
        scene = pj.get("scene", {})
        python = pj.get("python", {})
        deps = pj.get("deps", {})
        for dep in ("blender", "blendermcp", "codexcli"):
            if dep not in deps:
                raise ConfigError(f"config.json: deps.{dep} is required (used by run.py build)")
        return Settings(
            root=root,
            benchmark_name=bench["name"],
            benchmark_version=bench["version"],
            deps=deps,
            python_default=python.get("default", "3.12"),
            python_envs=python.get("envs", {"3.12": ".venv/py312", "3.14": ".venv/py314"}),
            codex_binary=codex.get("binary", "codex"),
            codex_sandbox=codex.get("sandbox", "workspace-write"),
            codex_extra_args=list(codex.get("extra_args", [])),
            codex_user=codex.get("user", "agent"),
            scene_reset=bool(scene.get("reset", True)),
            scene_save_blend=bool(scene.get("save_blend", True)),
            nag_prompt=pj.get("defaults", {}).get("nag_prompt") or DEFAULT_NAG_PROMPT,
            base_prompt=pj.get("defaults", {}).get("base_prompt") or DEFAULT_BASE_PROMPT,
            default_limits={**DEFAULT_LIMITS, **pj.get("defaults", {}).get("limits", {})},
            projects_dir=root / pj.get("projects_dir", "projects"),
            outputs_dir=root / pj.get("outputs_dir", "outputs"),
            env=load_dotenv(root / ".env"),
        )
    except KeyError as exc:
        raise ConfigError(f"config.json missing key: {exc}") from exc
