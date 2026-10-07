"""Scene manager contract: isolate and preserve the Blender scene per task."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..audit import Audit


class SceneManager(Protocol):
    def reset(self, audit: Audit) -> None:
        """Wipe the scene before a task. Must raise on failure: a dirty scene
        must fail the task rather than contaminate it."""
        ...

    def save(self, task_dir: Path, audit: Audit) -> str | None:
        """Save the scene work file into the task dir (best effort; None on failure)."""
        ...


class NullScene:
    """Scene management switched off (config.json scene.reset/save_blend = false)."""

    def reset(self, audit: Audit) -> None:
        audit.event("scene_reset", skipped="disabled in config.json")

    def save(self, task_dir: Path, audit: Audit) -> str | None:
        return None
