"""Project discovery and limit validation (green paths)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.projects import discover_projects, load_project, load_projects_file
from core.settings import ConfigError, load_settings


def _add_project(repo: Path, n: int, **task) -> None:
    d = repo / "projects" / f"project_{n}"
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    (d / f"project_{n}.py").write_text("# judge", encoding="utf-8")
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"].append({"id": f"project_{n}", "prompt": f"task {n}", **task})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")


def test_discover_list_order(repo: Path):
    _add_project(repo, 10)
    _add_project(repo, 2)
    s = load_settings(repo)
    # list order is preserved (benchmark authors control it)
    assert [p.id for p in discover_projects(s)] == ["project_1", "project_10", "project_2"]


def test_discover_only_filter(repo: Path):
    _add_project(repo, 2)
    s = load_settings(repo)
    assert [p.id for p in discover_projects(s, ["project_2"])] == ["project_2"]
    with pytest.raises(ConfigError, match="unknown project"):
        discover_projects(s, ["project_9"])


def test_disabled_projects_are_skipped(repo: Path):
    _add_project(repo, 2, enabled=False)
    s = load_settings(repo)
    assert [p.id for p in discover_projects(s)] == ["project_1"]


def test_duplicate_ids_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"].append({"id": "project_1", "prompt": "dup"})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    s = load_settings(repo)
    with pytest.raises(ConfigError, match="duplicate id"):
        discover_projects(s)


def test_project_load(repo: Path):
    entry = load_projects_file(repo)[0]
    p = load_project(repo / "projects", entry)
    assert p.id == "project_1" and p.judge.name == "project_1.py"
    assert p.dir == repo / "projects" / "project_1"


def test_limits_merge_and_defaults(repo: Path):
    _add_project(repo, 2, limits={"max_turns": 3})
    s = load_settings(repo)
    p2 = [p for p in discover_projects(s) if p.id == "project_2"][0]
    limits = p2.resolved_limits(s.default_limits)
    assert limits == {"min_rounds": 1, "max_turns": 3, "max_tokens": None}


def test_limits_all_unlimited_rejected(repo: Path):
    _add_project(repo, 2, limits={"min_rounds": None, "max_turns": None, "max_tokens": None})
    s = load_settings(repo)
    s.default_limits = {"min_rounds": None, "max_turns": None, "max_tokens": None}
    p2 = [p for p in discover_projects(s) if p.id == "project_2"][0]
    with pytest.raises(ConfigError, match="all unlimited"):
        p2.resolved_limits(s.default_limits)


def test_missing_directory_rejected(repo: Path):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"].append({"id": "project_9", "prompt": "ghost"})
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="not found"):
        discover_projects(load_settings(repo))
