"""Judge wrapper: run projects/project_N/project_N.py in the project's python env.

Contract: project_N.py --workspace <dir> --run <dir> --out <score.json>;
exit 0 and score.json = {score: 0-100, passed: bool, details: {...}}.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..audit import Audit
from ..projects import Project
from ..settings import ConfigError, Settings


class PythonJudge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, project: Project, task_dir: Path, audit: Audit) -> dict[str, Any]:
        judge_dir = task_dir / "judge"
        out_file = judge_dir / "score.json"
        try:
            py = self.settings.python_for(project.python)
        except ConfigError as exc:
            audit.event("judge_error", error=str(exc))
            return {"status": "judge_error", "error": str(exc)}
        # whitelist env: judges get what they need, not the controller's secrets
        keep = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG",
                "LC_ALL", "PYTHONIOENCODING")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env["PYTHONPATH"] = str(self.settings.root)
        env["MCP_URL"] = self.settings.mcp_url
        env["H3D_OUTPUTS_DIR"] = str(self.settings.outputs_dir.resolve())
        env["H3D_MCP_OUTPUTS_PREFIX"] = self.settings.env.get("H3D_MCP_OUTPUTS_PREFIX", "")
        cmd = [str(py), str(project.judge), "--workspace", str(task_dir / "workspace"),
               "--run", str(task_dir), "--out", str(out_file)]
        audit.event("judge_start", cmd=cmd)
        t0 = time.monotonic()
        with open(judge_dir / "stdout.log", "w", encoding="utf-8") as so, \
                open(judge_dir / "stderr.log", "w", encoding="utf-8") as se:
            try:
                proc = subprocess.run(cmd, cwd=project.dir, env=env, stdout=so, stderr=se,
                                      timeout=project.judge_timeout)
                code: int | None = proc.returncode
            except subprocess.TimeoutExpired:
                code = None
        seconds = round(time.monotonic() - t0, 1)
        if code is None:
            audit.event("judge_error", error=f"timeout after {project.judge_timeout}s")
            return {"status": "judge_error", "error": "timeout", "seconds": seconds}
        if code != 0:
            audit.event("judge_error", error=f"exit code {code}")
            return {"status": "judge_error", "error": f"judge exited with {code}", "seconds": seconds}
        try:
            score = json.loads(out_file.read_text(encoding="utf-8"))
            assert (isinstance(score.get("score"), (int, float))
                    and not isinstance(score.get("score"), bool) and 0 <= score["score"] <= 100)
            assert isinstance(score.get("passed"), bool)
        except Exception as exc:
            audit.event("judge_error", error=f"invalid score.json: {exc}")
            return {"status": "judge_error", "error": f"invalid score.json: {exc}", "seconds": seconds}
        score.update(status="judged", seconds=seconds)
        audit.event("judge_end", score=score["score"], passed=score["passed"], seconds=seconds)
        return score
