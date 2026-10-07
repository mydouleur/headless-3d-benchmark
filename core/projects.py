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
    enabled: bool = True

    def resolved_limits(self, defaults: dict[str, Any]) -> dict[str, Any]:
        merged = {**defaults, **self.limits}
        for key in ("min_rounds", "max_turns", "max_tokens"):
            v = merged.get(key)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                raise ConfigError(f"{self.id}: limits.{key} must be a positive integer or null")
            if v < 1:
                raise ConfigError(f"{self.id}: limits.{key} must be >= 1 or null")
        if merged.get("min_rounds") is None and merged.get("max_turns") is None \
                and merged.get("max_tokens") is None:
            raise ConfigError(
                f"{self.id}: limits are all unlimited — set at least one of "
                "min_rounds / max_turns / max_tokens")
        return merged


def load_projects_file(root: Path) -> list[dict[str, Any]]:
    pj = root / "projects.json"
    if not pj.is_file():
        raise ConfigError("projects.json not found (the task list)")
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"projects.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("projects.json: top level must be an object")
    entries = data.get("projects")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("projects.json: 'projects' must be a non-empty list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("projects.json: each entry must be an object")
    return entries


def load_project(projects_dir: Path, entry: dict[str, Any]) -> Project:
    pid = entry.get("id", "")
    if not re.fullmatch(r"project_\d+", str(pid)):
        raise ConfigError(f"projects.json: id must look like project_<number>, got {pid!r}")
    pdir = projects_dir / pid
    if not pdir.is_dir():
        raise ConfigError(f"{pid}: directory {pdir.relative_to(projects_dir.parent)}/ not found")
    if not entry.get("prompt") or not isinstance(entry.get("prompt"), str):
        raise ConfigError(f"{pid}: projects.json entry needs a prompt (string)")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{pid}: enabled must be true or false")
    judge = (pdir / entry.get("judge", f"{pid}.py")).resolve()
    if not judge.is_relative_to(pdir.resolve()):
        raise ConfigError(f"{pid}: judge must be a file inside {pdir.name}/ (no path traversal)")
    if not judge.is_file():
        raise ConfigError(f"{pid}: judge script not found: {judge.name}")
    if not (pdir / "workspace").is_dir():
        raise ConfigError(f"{pid}: missing workspace/ directory")
    judge_timeout = entry.get("judge_timeout", 600)
    if isinstance(judge_timeout, bool) or not isinstance(judge_timeout, (int, float)) \
            or judge_timeout <= 0:
        raise ConfigError(f"{pid}: judge_timeout must be a positive number of seconds")
    return Project(id=pid, dir=pdir, prompt=entry["prompt"], python=entry.get("python"),
                   judge=judge, judge_timeout=float(judge_timeout),
                   limits=entry.get("limits", {}), enabled=enabled)


def discover_projects(settings: Settings, only: list[str] | None = None) -> list[Project]:
    """Enabled tasks from projects.json, in list order; ids map to projects/<id>/."""
    entries = load_projects_file(settings.root)
    seen: set[str] = set()
    all_projects: list[Project] = []
    for entry in entries:
        pid = str(entry.get("id", ""))
        if pid in seen:
            raise ConfigError(f"projects.json: duplicate id {pid!r}")
        seen.add(pid)
        all_projects.append(load_project(settings.projects_dir, entry))
    if only:
        wanted = set(only)
        unknown = wanted - {p.id for p in all_projects}
        if unknown:
            raise ConfigError(f"unknown project(s): {sorted(unknown)}")
        disabled = wanted & {p.id for p in all_projects if not p.enabled}
        if disabled:
            raise ConfigError(f"project(s) disabled in projects.json: {sorted(disabled)}")
        return [p for p in all_projects if p.id in wanted]
    projects = [p for p in all_projects if p.enabled]
    if not projects:
        raise ConfigError("no enabled projects found in projects.json")
    return projects
