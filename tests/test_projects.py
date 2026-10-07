"""Project discovery and limit validation (green paths)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.projects import discover_projects, load_project
from core.settings import ConfigError


def _add_project(repo: Path, n: int, **task) -> None:
    d = repo / "projects" / f"project_{n}"
    (d / "workspace").mkdir(parents=True)
    (d / f"project_{n}.py").write_text("# judge", encoding="utf-8")
    (d / "task.json").write_text(json.dumps({"prompt": f"task {n}", **task}), encoding="utf-8")


def test_discover_numeric_order(repo: Path):
    _add_project(repo, 10)
    _add_project(repo, 2)
    s = __import__("core.settings", fromlist=["load_settings"]).load_settings(repo)
    assert [p.id for p in discover_projects(s)] == ["project_1", "project_2", "project_10"]


def test_discover_only_filter(repo: Path):
    _add_project(repo, 2)
    s = __import__("core.settings", fromlist=["load_settings"]).load_settings(repo)
    assert [p.id for p in discover_projects(s, ["project_2"])] == ["project_2"]
    with pytest.raises(ConfigError, match="unknown project"):
        discover_projects(s, ["project_9"])


def test_project_validation(repo: Path):
    p = load_project(repo / "projects" / "project_1")
    assert p.id == "project_1" and p.judge.name == "project_1.py"


def test_limits_merge_and_defaults(repo: Path):
    _add_project(repo, 2, limits={"max_turns": 3})
    s = __import__("core.settings", fromlist=["load_settings"]).load_settings(repo)
    p2 = [p for p in discover_projects(s) if p.id == "project_2"][0]
    limits = p2.resolved_limits(s.default_limits)
    assert limits == {"min_rounds": 1, "max_turns": 3, "max_tokens": None}


def test_limits_all_unlimited_rejected(repo: Path):
    _add_project(repo, 2, limits={"min_rounds": None, "max_turns": None, "max_tokens": None})
    s = __import__("core.settings", fromlist=["load_settings"]).load_settings(repo)
    s.default_limits = {"min_rounds": None, "max_turns": None, "max_tokens": None}
    p2 = [p for p in discover_projects(s) if p.id == "project_2"][0]
    with pytest.raises(ConfigError, match="all unlimited"):
        p2.resolved_limits(s.default_limits)


def test_missing_task_json_rejected(tmp_path: Path):
    d = tmp_path / "project_3"
    d.mkdir()
    with pytest.raises(ConfigError, match="task.json"):
        load_project(d)
