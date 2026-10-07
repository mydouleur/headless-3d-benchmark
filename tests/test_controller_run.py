"""End-to-end green test: fake codex + fake judge through the real run loop."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core import controller
from core.settings import Settings


@pytest.fixture(autouse=True)
def _system_python(monkeypatch: pytest.MonkeyPatch):
    """No .venv in the test checkout: judges run on the current interpreter."""
    monkeypatch.setattr(Settings, "python_for", lambda self, key=None: Path(sys.executable))


def _run(repo: Path, *extra: str, env: dict[str, str] | None = None,
         monkeypatch: pytest.MonkeyPatch) -> int:
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return controller.main(["--root", str(repo), "run", *extra])


def _only_result(repo: Path) -> tuple[Path, dict]:
    runs = list((repo / "outputs").iterdir())
    assert len(runs) == 1
    result = json.loads((runs[0] / "project_1" / "result.json").read_text(encoding="utf-8"))
    return runs[0], result


def test_full_run_green(repo: Path, monkeypatch, settings):
    assert _run(repo, monkeypatch=monkeypatch) == 0
    run_dir, result = _only_result(repo)

    # run dir name: <start>_<model>_v<benchmark-version>
    assert "_gpt-5-codex_v0.1.0" in run_dir.name
    # workspace copied, agent worked in the copy
    assert (run_dir / "project_1" / "workspace" / "hint.txt").is_file()
    assert (run_dir / "project_1" / "workspace" / "model.glb").is_file()
    # task.json snapshot kept
    assert (run_dir / "project_1" / "task.json").is_file()
    # raw round stream + audit trail
    round_log = (run_dir / "project_1" / "rounds" / "round_001.jsonl").read_text()
    assert "mcp_tool_call" in round_log and "turn.completed" in round_log
    audit = [json.loads(x) for x in
             (run_dir / "project_1" / "audit.jsonl").read_text().splitlines()]
    kinds = [e["type"] for e in audit]
    assert kinds[0] == "task_start" and kinds[-1] == "task_end"
    assert {"round_start", "mcp_call", "round_end", "agent_end", "judge_start", "judge_end"} \
        <= set(kinds)
    assert any(e["type"] == "mcp_call" and e["server"] == "blender" for e in audit)
    # result + judge + summary
    assert result["status"] == "completed" and result["rounds"] == 1
    assert result["tokens"] == 105 and result["score"] == 88.0 and result["passed"] is True
    assert result["answer_seconds"] >= 0
    summary = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "project_1" in summary and "88.00" in summary
    snap = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert snap["env"]["LLM_API_KEY"] == "***"  # secrets masked in the snapshot
    assert snap["mean_score"] == 88.0
    # codex config was generated into the redirected CODEX_HOME
    assert 'model = "gpt-5-codex"' in (repo / "codex-home" / "config.toml").read_text()


def test_min_rounds_triggers_nag_resume(repo: Path, monkeypatch, settings):
    log = repo / "codex_calls.jsonl"
    (repo / "projects" / "project_1" / "task.json").write_text(json.dumps({
        "id": "project_1", "prompt": "Make a mug.", "judge": "judge.py",
        "limits": {"min_rounds": 2, "max_turns": 5, "max_tokens": None},
    }), encoding="utf-8")
    assert _run(repo, monkeypatch=monkeypatch,
                env={"FAKE_CODEX_LOG": str(log)}) == 0
    _, result = _only_result(repo)
    assert result["status"] == "completed" and result["rounds"] == 2
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(calls) == 2
    assert "resume" not in calls[0] and calls[1][-3:-1] == ["resume", "--last"]


def test_max_turns_caps_open_ended_nags(repo: Path, monkeypatch, settings):
    (repo / "projects" / "project_1" / "task.json").write_text(json.dumps({
        "id": "project_1", "prompt": "Make a mug.", "judge": "judge.py",
        "limits": {"min_rounds": None, "max_turns": 3, "max_tokens": None},
    }), encoding="utf-8")
    assert _run(repo, monkeypatch=monkeypatch) == 1  # status max_turns != completed
    _, result = _only_result(repo)
    assert result["status"] == "max_turns" and result["rounds"] == 3


def test_max_tokens_limit(repo: Path, monkeypatch, settings):
    (repo / "projects" / "project_1" / "task.json").write_text(json.dumps({
        "id": "project_1", "prompt": "Make a mug.", "judge": "judge.py",
        "limits": {"min_rounds": None, "max_turns": None, "max_tokens": 50},
    }), encoding="utf-8")
    assert _run(repo, monkeypatch=monkeypatch, env={"FAKE_CODEX_BEHAVIOR": "many_tokens"}) == 1
    _, result = _only_result(repo)
    assert result["status"] == "max_tokens" and result["rounds"] == 1


def test_context_limit_ends_conversation(repo: Path, monkeypatch, settings):
    assert _run(repo, monkeypatch=monkeypatch, env={"FAKE_CODEX_BEHAVIOR": "context_limit"}) == 1
    _, result = _only_result(repo)
    assert result["status"] == "context_limit" and result["rounds"] == 1
    # judge still ran on whatever exists (recorded as evidence)
    assert result["judge"]["status"] == "judged"


def test_codex_error_is_recorded(repo: Path, monkeypatch, settings):
    assert _run(repo, monkeypatch=monkeypatch, env={"FAKE_CODEX_BEHAVIOR": "fail"}) == 1
    _, result = _only_result(repo)
    assert result["status"] == "codex_error"
