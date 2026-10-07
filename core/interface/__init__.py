"""Contracts between the controller and its pluggable wrappers.

AgentRunner   — drives one agent round (core/wrapper/codex.py)
SceneManager  — resets/saves the Blender scene around a task (core/wrapper/blender_mcp.py)
Judge         — runs a project's judge script (core/wrapper/python_judge.py)

The controller depends only on these interfaces; adding another agent CLI or
scene backend means adding a wrapper, not touching the orchestration.
"""
from .agent import AgentRunner, RoundResult
from .scene import NullScene, SceneManager
from .judge import Judge

__all__ = ["AgentRunner", "RoundResult", "SceneManager", "NullScene", "Judge"]
