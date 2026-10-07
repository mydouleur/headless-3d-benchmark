"""Red-team round 3: answer leakage + trust-boundary violations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core import controller
from core.settings import Settings


@pytest.fixture(autouse=True)
def _system_python(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Settings, "python_for", lambda self, key=None: Path(sys.executable))


EXFIL_CODEX = r"""
import json, sys
from pathlib import Path
# From the agent's workspace, walk up to the repo root and read the JUDGE
# SOURCE and the hidden reference answer. Nothing stops reads: codex's
# workspace-write sandbox restricts writes only, and in the controller image
# projects/ is baked in via COPY . . (.dockerignore does not exclude it).
here = Path.cwd()
root = next(p for p in here.parents if (p / "projects.json").is_file())
judge_src = (root / "projects" / "project_1" / "project_1.py").read_text(encoding="utf-8")
print(json.dumps({"type": "item.completed",
                  "item": {"type": "agent_message",
                           "text": "JUDGE SOURCE READ: " + judge_src[:400]}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
sys.exit(0)
"""


# R31 (CRITICAL): the agent can read the judge script (metric + threshold) and
# the reference answer from inside its workspace — the README claim
# "答案/判题不进入 agent 视野" does not hold. Proven here by reading the judge
# source from a fake agent; copying reference/mug.glb to model.glb is the
# same one-liner.
def test_r31_agent_reads_judge_and_reference(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "reference").mkdir()
    (repo / "projects" / "project_1" / "reference" / "mug.glb").write_bytes(b"SECRET-ANSWER")
    fake = repo / "fake_codex.py"
    fake.write_text(EXFIL_CODEX, encoding="utf-8")   # replace the fixture's fake
    rc = controller.main(["--root", str(repo), "run"])
    runs = list((repo / "outputs").iterdir())
    log = (runs[0] / "project_1" / "rounds" / "round_001.jsonl").read_text(encoding="utf-8")
    assert "JUDGE SOURCE READ" in log   # the agent read the judge script
    assert '\\"score\\": 88.0' in log   # ...including its hardcoded score logic
    assert rc == 0                      # ...and the run was scored as a success


ENV_DUMP_JUDGE = r"""
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--workspace"); ap.add_argument("--run"); ap.add_argument("--out")
args = ap.parse_args()
with open(args.out, "w", encoding="utf-8") as fh:
    json.dump({"score": 1.0, "passed": False,
               "details": {"saw_api_key": bool(os.environ.get("OPENAI_API_KEY"))}}, fh)
"""


# R32: the judge subprocess inherits the controller's FULL environment,
# including the live LLM API key it has no use for
def test_r32_judge_inherits_api_key(repo: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
    (repo / "projects" / "project_1" / "project_1.py").write_text(ENV_DUMP_JUDGE, encoding="utf-8")
    controller.main(["--root", str(repo), "run"])
    runs = list((repo / "outputs").iterdir())
    score = json.loads((runs[0] / "project_1" / "judge" / "score.json").read_text(encoding="utf-8"))
    assert score["details"]["saw_api_key"] is True
