"""Project discovery: projects/project_N/{task.json, workspace/, project_N.py}."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import ConfigError, Settings


@dataclass
class Project:
    id: str
    dir: Path
    prompt: str
    python: str | None
    judge: Path
    judge_timeout: float
    limits: dict[str, Any]

    def resolved_limits(self, defaults: dict[str, Any]) -> dict[str, Any]:
        merged = {**defaults, **self.limits}
        if merged.get("min_rounds") is None and merged.get("max_turns") is None \
                and merged.get("max_tokens") is None:
            raise ConfigError(
                f"{self.id}: limits are all unlimited — set at least one of "
                "min_rounds / max_turns / max_tokens")
        if merged.get("min_rounds") is not None and merged["min_rounds"] < 0:
            raise ConfigError(f"{self.id}: min_rounds must be >= 0 or null")
        return merged


def load_project(pdir: Path) -> Project:
    tj = pdir / "task.json"
    if not tj.is_file():
        raise ConfigError(f"{pdir}: missing task.json")
    data = json.loads(tj.read_text(encoding="utf-8"))
    pid = data.get("id") or pdir.name
    if not re.fullmatch(r"project_\d+", pid):
        raise ConfigError(f"{pdir}: id must look like project_<number>, got {pid!r}")
    if not data.get("prompt"):
        raise ConfigError(f"{pdir}: task.json needs a prompt")
    judge = pdir / data.get("judge", f"{pid}.py")
    if not judge.is_file():
        raise ConfigError(f"{pdir}: judge script not found: {judge.name}")
    if not (pdir / "workspace").is_dir():
        raise ConfigError(f"{pdir}: missing workspace/ directory")
    return Project(id=pid, dir=pdir, prompt=data["prompt"], python=data.get("python"),
                   judge=judge, judge_timeout=float(data.get("judge_timeout", 600)),
                   limits=data.get("limits", {}))


def discover_projects(settings: Settings, only: list[str] | None = None) -> list[Project]:
    if not settings.projects_dir.is_dir():
        raise ConfigError(f"projects dir not found: {settings.projects_dir}")
    dirs = sorted((d for d in settings.projects_dir.iterdir()
                   if d.is_dir() and re.fullmatch(r"project_\d+", d.name)),
                  key=lambda d: int(d.name.rsplit("_", 1)[1]))
    projects = [load_project(d) for d in dirs]
    if only:
        wanted = set(only)
        unknown = wanted - {p.id for p in projects}
        if unknown:
            raise ConfigError(f"unknown project(s): {sorted(unknown)}")
        projects = [p for p in projects if p.id in wanted]
    if not projects:
        raise ConfigError("no projects found")
    return projects
