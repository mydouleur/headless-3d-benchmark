"""Judge contract: score one finished task."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..audit import Audit
from ..projects import Project


class Judge(Protocol):
    def run(self, project: Project, task_dir: Path, audit: Audit) -> dict[str, Any]:
        """Run the project's judge script. Returns a dict with at least
        ``status``: "judged" (plus score/passed) or "judge_error" (plus error)."""
        ...
