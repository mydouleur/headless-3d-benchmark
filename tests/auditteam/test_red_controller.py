"""Red-team: controller orchestration + codex event stream adversarial cases.

auditteam convention: assert the DESIRED behavior; a failure is an open
finding for the dev team. R10-R21 were exploited in the first audit round
(problem.md) and flipped to security-regression assertions after T17.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from core import controller
from core.settings import Settings
from core.wrapper.codex import parse_codex_event


@pytest.fixture(autouse=True)
def _system_python(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Settings, "python_for", lambda self, key=None: Path(sys.executable))


def _run(repo: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str] | None = None) -> int:
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return controller.main(["--root", str(repo), "run"])


def _only_result(repo: Path) -> tuple[Path, dict]:
    runs = list((repo / "outputs").iterdir())
    assert len(runs) == 1
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    return runs[0], result


# R10: a poisonous usage event must degrade to 0 tokens, not crash the round
def test_r10_bad_usage_event_degrades(repo: Path, monkeypatch):
    rc = _run(repo, monkeypatch, {"FAKE_CODEX_BEHAVIOR": "bad_usage"})
    _, result = _only_result(repo)
    assert result["status"] == "completed"
    assert result["tokens"] == 5   # dirty field degraded to 0, valid sibling kept
    assert rc == 0


# R11: an invalid-base64 image block must be dropped, not crash the round
def test_r11_bad_image_dropped(repo: Path, monkeypatch):
    rc = _run(repo, monkeypatch, {"FAKE_CODEX_BEHAVIOR": "bad_image"})
    run_dir, result = _only_result(repo)
    assert result["status"] == "completed" and rc == 0
    audit = [json.loads(x) for x in
             (run_dir / "project_1" / "audit.jsonl").read_text().splitlines()]
    assert any(e["type"] == "image_dropped" for e in audit)


# R12: if event processing ever blows up mid-stream, the codex child must be
# killed and reaped (fixed in T17 via try/kill/wait; parser is monkeypatched
# here to simulate any future crash source)
def test_r12_child_killed_on_processing_crash(repo: Path, monkeypatch, settings):
    import core.wrapper.codex as cw
    from core.audit import Audit

    monkeypatch.setattr(cw, "parse_codex_event",
                        lambda line: (_ for _ in ()).throw(RuntimeError("parser boom")))
    task_dir = repo / "task"
    (task_dir / "rounds").mkdir(parents=True)
    (task_dir / "workspace").mkdir()
    audit = Audit(task_dir / "audit.jsonl")
    monkeypatch.setenv("FAKE_CODEX_BEHAVIOR", "slow")  # child sleeps 30s if not killed

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="parser boom"):
        cw.CodexWrapper(settings).run_round(task_dir / "workspace", task_dir, "hi", 1, audit)
    assert time.monotonic() - t0 < 10   # child killed, not left running
    audit.close()


# R13: judge failure must fail the run (exit 1), even when the agent completed
def test_r13_judge_error_fails_run(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import sys; sys.exit(3)", encoding="utf-8")
    rc = _run(repo, monkeypatch)
    _, result = _only_result(repo)
    assert result["judge"]["status"] == "judge_error"
    assert "exited with 3" in result["judge"]["error"]
    assert result["status"] == "completed"
    assert rc == 1


# R14: score.json violating the contract -> judge_error (correct behavior, pinned)
def test_r14_judge_invalid_score_json(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import argparse, json\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--workspace'); ap.add_argument('--run'); ap.add_argument('--out')\n"
        "a = ap.parse_args()\n"
        "open(a.out, 'w').write(json.dumps({'score': 500, 'passed': True}))\n",
        encoding="utf-8")
    _run(repo, monkeypatch)
    _, result = _only_result(repo)
    assert result["judge"]["status"] == "judge_error"
    assert "invalid score.json" in result["judge"]["error"]


# R15: bool must not pass as a numeric score (bool is an int subclass)
def test_r15_judge_score_bool_rejected(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import argparse, json\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--workspace'); ap.add_argument('--run'); ap.add_argument('--out')\n"
        "a = ap.parse_args()\n"
        "open(a.out, 'w').write(json.dumps({'score': True, 'passed': True}))\n",
        encoding="utf-8")
    _run(repo, monkeypatch)
    _, result = _only_result(repo)
    assert result["judge"]["status"] == "judge_error"


# R16: judge that never writes score.json but exits 0 -> judge_error (pinned)
def test_r16_judge_missing_score_file(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text("pass\n", encoding="utf-8")
    _run(repo, monkeypatch)
    _, result = _only_result(repo)
    assert result["judge"]["status"] == "judge_error"


# R17: workspace copy failure must leave a result and must NOT kill the run
def test_r17_prepare_failure_isolated_per_task(repo: Path, monkeypatch):
    monkeypatch.setattr(controller.shutil, "copytree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    rc = _run(repo, monkeypatch)
    run_dir, result = _only_result(repo)
    assert result["status"] == "error" and "prepare failed" in result["error"]
    assert (run_dir / "summary.md").is_file()
    assert rc == 1


# R18: judge subprocess timeout -> judge_error (pinned)
def test_r18_judge_timeout(repo: Path, monkeypatch):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["judge_timeout"] = 1
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import time; time.sleep(30)", encoding="utf-8")
    _run(repo, monkeypatch)
    _, result = _only_result(repo)
    assert result["judge"]["status"] == "judge_error"
    assert result["judge"]["error"] == "timeout"


# R19: negative judge_timeout must be rejected at load time (fixed in T17)
def test_r19_negative_judge_timeout_rejected(repo: Path):
    from core.settings import ConfigError, load_settings
    from core.projects import discover_projects
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["judge_timeout"] = -5
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="judge_timeout"):
        discover_projects(load_settings(repo))


# R20: mcp_path must not translate sibling dirs sharing a string prefix
def test_r20_mcp_path_prefix_collision(settings):
    outputs = settings.outputs_dir.resolve()
    sibling = Path(str(outputs) + "_evil") / "scene.blend"
    settings.env["H3D_MCP_OUTPUTS_PREFIX"] = "/app/outputs"
    assert settings.mcp_path(sibling) == str(sibling.resolve())


# R21: unknown usage key shapes must surface in the audit log, not vanish
def test_r21_unknown_usage_keys_audited(repo: Path, monkeypatch):
    ev = json.dumps({"type": "turn.completed", "usage": {"total_tokens": 5000}})
    info = parse_codex_event(ev)
    assert info["usage"] == {"input": 0, "output": 0, "cached": 0}
    assert info.get("usage_unknown") == ["total_tokens"]   # visibility, not silence
