"""Red-team: controller orchestration + codex event stream adversarial cases."""
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


# R10: poisonous usage event crashes run_round with ValueError (not handled)
def test_r10_bad_usage_event_crashes_round(repo: Path, monkeypatch):
    rc = _run(repo, monkeypatch, {"FAKE_CODEX_BEHAVIOR": "bad_usage"})
    # run_task catches Exception -> status "error"; the codex event parser blew up
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "error"
    assert "ValueError" in result["error"]
    assert rc == 1


# R11: invalid base64 in an image block crashes event processing the same way
def test_r11_bad_image_b64_crashes_round(repo: Path, monkeypatch):
    rc = _run(repo, monkeypatch, {"FAKE_CODEX_BEHAVIOR": "bad_image"})
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "error"
    assert rc == 1


# R12: when event processing blows up mid-stream, the codex child is never reaped
def test_r12_child_process_orphaned_on_parser_crash(repo: Path, monkeypatch, settings):
    from core.audit import Audit
    from core.wrapper.codex import CodexWrapper

    task_dir = repo / "task"
    (task_dir / "rounds").mkdir(parents=True)
    (task_dir / "workspace").mkdir()
    audit = Audit(task_dir / "audit.jsonl")
    monkeypatch.setenv("FAKE_CODEX_BEHAVIOR", "slow_after_bad_usage")

    procs_before = None
    wrapper = CodexWrapper(settings)
    t0 = time.monotonic()
    with pytest.raises(ValueError):
        wrapper.run_round(task_dir / "workspace", task_dir, "hi", 1, audit)
    elapsed = time.monotonic() - t0
    # If the wrapper had waited for/killed the child, this would take ~30s.
    # It returned instantly: the 30s child is still running, un-reaped.
    assert elapsed < 10
    audit.close()
    # the orphaned fake-codex child sleeps 30s and exits on its own


# R13: judge exit != 0 -> judge_error (path exists but untested upstream)
def test_r13_judge_nonzero_exit(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import sys; sys.exit(3)", encoding="utf-8")
    rc = _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["judge"]["status"] == "judge_error"
    assert "exited with 3" in result["judge"]["error"]
    # FINDING: judge failure does not affect the process exit code — the task
    # status is "completed" (agent finished), so cmd_run returns 0 and CI sees
    # a successful run with zero scored projects.
    assert result["status"] == "completed"
    assert rc == 0


# R14: judge writes score.json violating the contract -> judge_error
def test_r14_judge_invalid_score_json(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import argparse, json\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--workspace'); ap.add_argument('--run'); ap.add_argument('--out')\n"
        "a = ap.parse_args()\n"
        "open(a.out, 'w').write(json.dumps({'score': 500, 'passed': True}))\n",
        encoding="utf-8")
    _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["judge"]["status"] == "judge_error"
    assert "invalid score.json" in result["judge"]["error"]


# R15: bool is an int subclass — score:true passes validation as a numeric score
def test_r15_judge_score_bool_accepted(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import argparse, json\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--workspace'); ap.add_argument('--run'); ap.add_argument('--out')\n"
        "a = ap.parse_args()\n"
        "open(a.out, 'w').write(json.dumps({'score': True, 'passed': True}))\n",
        encoding="utf-8")
    _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["judge"]["status"] == "judged"
    assert result["score"] is True  # score == True (1) silently accepted


# R16: judge that never writes score.json but exits 0 -> judge_error (contract hole shown)
def test_r16_judge_missing_score_file(repo: Path, monkeypatch):
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "pass\n", encoding="utf-8")
    _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["judge"]["status"] == "judge_error"


# R17: workspace copy failure kills the WHOLE run — no result.json, no summary,
# remaining projects never run; exception escapes main() as a traceback
def test_r17_prepare_task_dir_failure_crushes_run(repo: Path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "copytree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):  # not caught by main(): traceback, no partial results
        _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    assert len(runs) == 1
    assert not (runs[0] / "project_1" / "result.json").exists()
    assert not (runs[0] / "summary.md").exists()


# R18: judge subprocess timeout -> judge_error (untested upstream path)
def test_r18_judge_timeout(repo: Path, monkeypatch):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["judge_timeout"] = 1
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    (repo / "projects" / "project_1" / "project_1.py").write_text(
        "import time; time.sleep(30)", encoding="utf-8")
    _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    assert result["judge"]["status"] == "judge_error"
    assert result["judge"]["error"] == "timeout"


# R19: negative judge_timeout -> ValueError inside subprocess, uncaught by judge wrapper?
def test_r19_negative_judge_timeout(repo: Path, monkeypatch):
    data = json.loads((repo / "projects.json").read_text(encoding="utf-8"))
    data["projects"][0]["judge_timeout"] = -5
    (repo / "projects.json").write_text(json.dumps(data), encoding="utf-8")
    _run(repo, monkeypatch)
    runs = list((repo / "outputs").iterdir())
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    # document actual behavior: subprocess treats timeout<=0 as immediate expiry
    assert result["judge"]["status"] == "judge_error"


# R20: mcp_path prefix matching is a plain string prefix — sibling dirs get mistranslated
def test_r20_mcp_path_prefix_collision(settings, monkeypatch):
    outputs = settings.outputs_dir.resolve()
    sibling = Path(str(outputs) + "_evil") / "scene.blend"
    settings.env["H3D_MCP_OUTPUTS_PREFIX"] = "/app/outputs"
    translated = settings.mcp_path(sibling)
    # sibling is OUTSIDE the outputs dir, but startswith() matches anyway
    assert translated.startswith("/app/outputs")


# R21: parse_codex_event with usage=null fields / unknown shapes stays silent —
# token accounting can silently under-count (max_tokens never triggers)
def test_r21_usage_variants_undercount():
    # codex variants that use different key names contribute 0 tokens silently
    ev = json.dumps({"type": "turn.completed",
                     "usage": {"total_tokens": 5000}})
    assert parse_codex_event(ev)["usage"] == {"input": 0, "output": 0, "cached": 0}
