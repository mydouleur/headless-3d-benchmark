"""Red-team: answer isolation + trust boundaries.

auditteam convention: assert the DESIRED behavior; a failure is an open
finding for the dev team.
"""
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
# Host mode (no root / no container): nothing separates the agent from the
# judge sources. This test documents that HOST-MODE limitation — it is
# expected to pass here and would only fail inside the hardened container
# if the isolation regressed there (projects/ is root-only, codex runs as
# the unprivileged agent user via setpriv).
here = Path.cwd()
root = next(p for p in here.parents if (p / "projects.json").is_file())
judge_src = (root / "projects" / "project_1" / "project_1.py").read_text(encoding="utf-8")
print(json.dumps({"type": "item.completed",
                  "item": {"type": "agent_message",
                           "text": "JUDGE SOURCE READ: " + judge_src[:400]}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
sys.exit(0)
"""


# R31: HOST-mode demonstration — without the container's privilege drop the
# agent can read judge sources. The production fix (Dockerfile chmod o-rwx
# projects/ + setpriv to codex.user) is verified statically; README documents
# that host runs have no isolation. Keep this as the canary: if someone runs
# this suite AS ROOT on Linux, it must still pass only because tests are root.
def test_r31_host_mode_has_no_answer_isolation(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "reference").mkdir()
    (repo / "projects" / "project_1" / "reference" / "mug.glb").write_bytes(b"SECRET-ANSWER")
    fake = repo / "fake_codex.py"
    fake.write_text(EXFIL_CODEX, encoding="utf-8")   # replace the fixture's fake
    rc = controller.main(["--root", str(repo), "run"])
    run_dir = next((repo / "outputs").iterdir())
    log = (run_dir / "project_1" / "rounds" / "round_001.jsonl").read_text(encoding="utf-8")
    assert "JUDGE SOURCE READ" in log   # host mode: still readable, as documented
    assert rc == 0


# R31b: the container-side isolation must be wired up (static check of the
# Dockerfile contract — the real enforcement needs Docker, unavailable here)
def test_r31b_container_isolation_wired():
    dockerfile = (Path(__file__).resolve().parent.parent.parent / "Dockerfile").read_text()
    assert "useradd -m agent" in dockerfile          # unprivileged user exists
    assert "chmod -R o-rwx /app/projects" in dockerfile  # answers root-only
    compose = (Path(__file__).resolve().parent.parent.parent
               / "docker-compose.yml").read_text()
    assert "127.0.0.1" in compose                    # MCP port not LAN-exposed


ENV_DUMP_JUDGE = r"""
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--workspace"); ap.add_argument("--run"); ap.add_argument("--out")
args = ap.parse_args()
with open(args.out, "w", encoding="utf-8") as fh:
    json.dump({"score": 1.0, "passed": False,
               "details": {"saw_api_key": bool(os.environ.get("OPENAI_API_KEY"))}}, fh)
"""


# R32: the judge subprocess must NOT inherit the controller's secrets
# (fixed in T17 via env whitelist)
def test_r32_judge_env_is_whitelisted(repo: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
    (repo / "projects" / "project_1" / "project_1.py").write_text(ENV_DUMP_JUDGE, encoding="utf-8")
    controller.main(["--root", str(repo), "run"])
    run_dir = next((repo / "outputs").iterdir())
    score = json.loads((run_dir / "project_1" / "judge" / "score.json").read_text(encoding="utf-8"))
    assert score["details"]["saw_api_key"] is False
