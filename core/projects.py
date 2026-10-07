"""Project discovery: root projects.json lists the tasks; each id maps to
projects/<id>/{workspace/, reference/, <id>.py}."""
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


def load_projects_file(root: Path) -> list[dict[str, Any]]:
    pj = root / "projects.json"
    if not pj.is_file():
        raise ConfigError("projects.json not found (the task list)")
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"projects.json is not valid JSON: {exc}") from exc
    entries = data.get("projects")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("projects.json: 'projects' must be a non-empty list")
    return entries


def load_project(root: Path, entry: dict[str, Any]) -> Project:
    pid = entry.get("id", "")
    if not re.fullmatch(r"project_\d+", pid):
        raise ConfigError(f"projects.json: id must look like project_<number>, got {pid!r}")
    pdir = root / "projects" / pid
    if not pdir.is_dir():
        raise ConfigError(f"{pid}: directory projects/{pid}/ not found")
    if not entry.get("prompt"):
        raise ConfigError(f"{pid}: projects.json entry needs a prompt")
    judge = pdir / entry.get("judge", f"{pid}.py")
    if not judge.is_file():
        raise ConfigError(f"{pid}: judge script not found: {judge.name}")
    if not (pdir / "workspace").is_dir():
        raise ConfigError(f"{pid}: missing workspace/ directory")
    return Project(id=pid, dir=pdir, prompt=entry["prompt"], python=entry.get("python"),
                   judge=judge, judge_timeout=float(entry.get("judge_timeout", 600)),
                   limits=entry.get("limits", {}))


def discover_projects(settings: Settings, only: list[str] | None = None) -> list[Project]:
    """Enabled tasks from projects.json, in list order; ids map to projects/<id>/."""
    entries = load_projects_file(settings.root)
    seen: set[str] = set()
    projects: list[Project] = []
    for entry in entries:
        pid = entry.get("id", "")
        if pid in seen:
            raise ConfigError(f"projects.json: duplicate id {pid!r}")
        seen.add(pid)
        if entry.get("enabled", True) is False:
            continue
        projects.append(load_project(settings.root, entry))
    if only:
        wanted = set(only)
        unknown = wanted - {p.id for p in projects}
        if unknown:
            raise ConfigError(f"unknown project(s): {sorted(unknown)}")
        projects = [p for p in projects if p.id in wanted]
    if not projects:
        raise ConfigError("no enabled projects found in projects.json")
    return projects
