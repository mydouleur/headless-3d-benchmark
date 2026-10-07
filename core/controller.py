"""Master orchestration + CLI.

Pipeline per run:
  1. load config.json + projects.json + .env, write the codex config
  2. create outputs/<start>_<model>_<benchmark-version>/ and copy every
     projects/project_N/workspace into <run>/project_N/workspace
  3. per project (serial): scene reset -> agent rounds until done/limited ->
     save scene.blend -> judge with the project's python env
  4. result.json per project; run.json / summary.md at run level
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import Audit
from .interface import AgentRunner, Judge, NullScene, SceneManager
from .projects import Project, discover_projects
from .settings import ConfigError, Settings, load_settings
from .wrapper import BlenderMcpScene, CodexWrapper, PythonJudge

ROOT = Path(__file__).resolve().parent.parent


def build_wrappers(settings: Settings) -> tuple[AgentRunner, SceneManager, Judge]:
    agent: AgentRunner = CodexWrapper(settings)
    scene: SceneManager = BlenderMcpScene(settings) if (settings.scene_reset or settings.scene_save_blend) \
        else NullScene()
    judge: Judge = PythonJudge(settings)
    return agent, scene, judge


# ---------------------------------------------------------------------------
# Run directory
# ---------------------------------------------------------------------------
def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "-", text).strip("-") or "model"


def create_run_dir(settings: Settings, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = settings.outputs_dir / f"{stamp}_{safe_name(settings.model)}_v{settings.benchmark_version}"
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = Path(f"{base}_{n}")
    run_dir.mkdir(parents=True)
    return run_dir


def prepare_task_dir(project: Project, run_dir: Path) -> Path:
    """Copy the project's workspace into the run dir; the agent only works there."""
    task_dir = run_dir / project.id
    shutil.copytree(project.dir / "workspace", task_dir / "workspace")
    (task_dir / "rounds").mkdir(parents=True)
    (task_dir / "judge").mkdir()
    (task_dir / "task.json").write_text(json.dumps(
        {"id": project.id, "prompt": project.prompt, "python": project.python,
         "judge": project.judge.name, "judge_timeout": project.judge_timeout,
         "limits": project.limits}, indent=2, ensure_ascii=False), encoding="utf-8")
    return task_dir


# ---------------------------------------------------------------------------
# Task loop (serial; one task = copy workspace -> agent rounds -> judge)
# ---------------------------------------------------------------------------
def agent_phase(agent: AgentRunner, project: Project, task_dir: Path,
                defaults: dict[str, Any], nag_prompt: str, audit: Audit) -> dict[str, Any]:
    """Drive the agent until done or a limit hits (limits are AND-ed: the first
    one reached ends the conversation; each may be null = unlimited)."""
    limits = project.resolved_limits(defaults)
    workspace = task_dir / "workspace"
    rounds, tokens = 0, 0
    status, error, final_message = "completed", None, ""
    while True:
        if limits["max_turns"] is not None and rounds >= limits["max_turns"]:
            status = "max_turns"
            break
        prompt = project.prompt if rounds == 0 else nag_prompt
        r = agent.run_round(workspace, task_dir, prompt, rounds + 1, audit)
        rounds += 1
        tokens += r.total_tokens
        final_message = r.final_message or final_message
        if r.context_limited:
            status, error = "context_limit", r.error
            break
        if r.exit_code != 0:
            status, error = "codex_error", r.error or f"exit code {r.exit_code}"
            break
        if limits["max_tokens"] is not None and tokens >= limits["max_tokens"]:
            status = "max_tokens"
            break
        min_rounds = limits["min_rounds"]
        if min_rounds is not None and rounds >= max(min_rounds, 1):
            break  # agent said it is done and min_rounds is satisfied
        # else (min_rounds null = open-ended, or not reached yet): nag it to
        # continue with plain text — never the score
    audit.event("agent_end", status=status, rounds=rounds, tokens=tokens, error=error)
    return {"status": status, "rounds": rounds, "tokens": tokens,
            "error": error, "final_message": final_message, "limits": limits}


def run_task(settings: Settings, agent: AgentRunner, scene: SceneManager, judge: Judge,
             project: Project, run_dir: Path) -> dict[str, Any]:
    task_dir = prepare_task_dir(project, run_dir)
    audit = Audit(task_dir / "audit.jsonl")
    t0 = time.monotonic()
    result: dict[str, Any] = {"id": project.id, "status": "pending"}
    audit.event("task_start", id=project.id, prompt_length=len(project.prompt))
    try:
        if settings.scene_reset:
            try:
                scene.reset(audit)
            except Exception as exc:
                result.update(status="scene_error", error=str(exc))
                return _finish_task(result, task_dir, audit, t0)
        result.update(agent_phase(agent, project, task_dir, settings.default_limits,
                                  settings.nag_prompt, audit))
        if settings.scene_save_blend:
            if scene.save(task_dir, audit):
                result["scene_blend"] = "scene.blend"
    except KeyboardInterrupt:
        result.update(status="interrupted")
        _finish_task(result, task_dir, audit, t0)
        raise
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
        audit.event("task_error", error=result["error"])
    judged = judge.run(project, task_dir, audit)
    result["judge"] = judged
    if judged.get("status") == "judged":
        result["score"] = judged["score"]
        result["passed"] = judged["passed"]
    return _finish_task(result, task_dir, audit, t0)


def _finish_task(result: dict[str, Any], task_dir: Path, audit: Audit, t0: float) -> dict[str, Any]:
    result["answer_seconds"] = round(time.monotonic() - t0, 1)
    (task_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    audit.event("task_end", status=result["status"], answer_seconds=result["answer_seconds"],
                score=result.get("score"))
    audit.close()
    return result


# ---------------------------------------------------------------------------
# Run level
# ---------------------------------------------------------------------------
def masked_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if re.search(r"KEY|TOKEN|SECRET", k) else v) for k, v in env.items()}


def git_rev(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def write_summary(settings: Settings, run_dir: Path, results: list[dict[str, Any]],
                  started: datetime) -> None:
    finished = datetime.now().isoformat(timespec="seconds")
    scores = [r["score"] for r in results if r.get("score") is not None]
    mean = sum(scores) / len(scores) if scores else None
    run_json = run_dir / "run.json"
    snap = json.loads(run_json.read_text(encoding="utf-8"))
    snap["finished"] = finished
    snap["mean_score"] = mean
    run_json.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    fmt = lambda v: "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))  # noqa: E731
    lines = [f"# {settings.benchmark_name} v{settings.benchmark_version}", "",
             f"model `{settings.model}`, started {started.isoformat(timespec='seconds')}", "",
             "| project | status | score | rounds | tokens | answer time |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['status']} | {fmt(r.get('score'))} | {r.get('rounds', '-')} | "
                     f"{r.get('tokens', '-')} | {r.get('answer_seconds', '-')}s |")
    lines += ["", f"Mean score: {fmt(mean)} over {len(scores)} judged project(s)."]
    for r in results:
        if r.get("error"):
            lines.append(f"\n- `{r['id']}`: {r['error']}")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.root)
    projects = discover_projects(settings, args.project)
    agent, scene, judge = build_wrappers(settings)
    agent.write_config()
    run_dir = create_run_dir(settings)
    started = datetime.now()
    (run_dir / "run.json").write_text(json.dumps({
        "benchmark": {"name": settings.benchmark_name, "version": settings.benchmark_version},
        "model": settings.model, "deps": settings.deps, "env": masked_env(settings.env),
        "git": git_rev(settings.root), "started": started.isoformat(timespec="seconds"),
        "projects": [p.id for p in projects],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"run directory: {run_dir}")
    results = []
    for p in projects:  # serial on purpose: one agent + one Blender at a time
        print(f"=== {p.id} ===", flush=True)
        results.append(run_task(settings, agent, scene, judge, p, run_dir))
        write_summary(settings, run_dir, results, started)
    write_summary(settings, run_dir, results, started)
    print((run_dir / "summary.md").read_text(encoding="utf-8"))
    ok = all(r["status"] == "completed" for r in results)
    return 0 if ok else 1


def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings(args.root)
    ok = True
    print(f"benchmark: {settings.benchmark_name} v{settings.benchmark_version}")
    print(f"deps: {settings.deps}")
    print(f"model: {settings.model or '(LLM_MODEL not set)'}")
    if not (settings.env.get("LLM_API_KEY")
            or settings.env.get(settings.env.get("LLM_ENV_KEY", "OPENAI_API_KEY"))):
        print("WARNING: no API key set (.env LLM_API_KEY or the provider env_key)")
    for p in discover_projects(settings, args.project):
        try:
            limits = p.resolved_limits(settings.default_limits)
            settings.python_for(p.python)
            print(f"[{p.id}] judge={p.judge.name} python={p.python or settings.python_default} "
                  f"limits={limits}")
        except ConfigError as exc:
            print(f"[{p.id}] ERROR: {exc}")
            ok = False
    if shutil.which(settings.codex_binary) or Path(settings.codex_binary).is_file():
        print(f"codex: {settings.codex_binary}")
    else:
        print(f"WARNING: codex binary not found: {settings.codex_binary}")
    print("--- generated codex config.toml ---")
    print(CodexWrapper(settings).config_toml())
    print("OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def cmd_envinfo(args: argparse.Namespace) -> int:
    settings = load_settings(args.root)
    print(f"default python: {settings.python_default}")
    for key, venv in settings.python_envs.items():
        exe = settings.root / venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        print(f"  {key}: {venv} ({'installed' if exe.is_file() else 'missing — run envinstall.py'})")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Build docker images with the versions pinned in config.json."""
    settings = load_settings(args.root)
    env = dict(os.environ)
    env.update({"BLENDER_VERSION": settings.deps["blender"],
                "BLENDERMCP_VERSION": settings.deps["blendermcp"],
                "CODEX_VERSION": settings.deps["codexcli"]})
    cmd = ["docker", "compose", "build"] + list(args.args or [])
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=settings.root, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--project", action="append", help="only this project (repeatable)")
    sub.add_parser("envinfo")
    sp = sub.add_parser("build", help="docker compose build with config.json versions")
    sp.add_argument("args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    try:
        return {"run": cmd_run, "check": cmd_check, "envinfo": cmd_envinfo,
                "build": cmd_build}[args.command](args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; partial results are kept in the run directory", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
