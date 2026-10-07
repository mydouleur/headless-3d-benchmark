#!/usr/bin/env python3
"""headless-3d-bench master controller (single file).

Pipeline per run:
  1. load project.json + .env, generate ~/.codex/config.toml for Codex CLI
  2. create outputs/<start>_<model>_<benchmark-version>/ and copy every
     projects/project_N/workspace into <run>/project_N/workspace
  3. per project (serial): reset the Blender scene via BlenderMCP, run Codex
     rounds (codex exec / codex exec resume --last) until the agent is done or
     a limit hits, save scene.blend, then run the project's judge script with
     the python environment the project picked
  4. write result.json per project and run.json / summary.md at run level

Everything the agent did is auditable: raw codex --json event streams per
round, stderr logs, extracted MCP tool calls, controller audit.jsonl, the
scene file and the judge output are all kept under the run directory.

Usage:
  python controller.py run [--project project_1 ...] [--root DIR]
  python controller.py check          # validate config/projects, no agent calls
  python controller.py envinfo        # show resolved environments/versions
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

NAG_PROMPT = ("还没有达到要求。请检查你当前的模型：和参考输入逐项对比形状、比例和部件位置，"
              "继续改进；确认模型仍然导出在同一个 glb 路径。完成后回复一段简短总结。")

CONTEXT_LIMIT_RE = re.compile(
    r"context[_ ]?(length|window|limit)|maximum context|too many tokens|context overflow",
    re.IGNORECASE)

DEFAULT_LIMITS = {"min_rounds": 1, "max_turns": 10, "max_tokens": None}


class ConfigError(ValueError):
    """Invalid project.json / .env / project task configuration."""


# ---------------------------------------------------------------------------
# Settings: .env + project.json
# ---------------------------------------------------------------------------
def load_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines; real environment variables win over the file."""
    data: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            data[key] = value.strip().strip("'\"")
    merged = dict(data)
    merged.update({k: v for k, v in os.environ.items() if k in data or k in (
        "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_ENV_KEY", "LLM_WIRE_API",
        "LLM_MODEL", "MCP_URL", "OPENAI_API_KEY", "OPENROUTER_API_KEY")})
    return merged


@dataclass
class Settings:
    root: Path
    benchmark_name: str
    benchmark_version: str
    deps: dict[str, str]
    python_default: str
    python_envs: dict[str, str]
    codex_binary: str
    codex_sandbox: str
    codex_extra_args: list[str]
    scene_reset: bool
    scene_save_blend: bool
    default_limits: dict[str, Any]
    projects_dir: Path
    outputs_dir: Path
    env: dict[str, str]

    @property
    def model(self) -> str:
        return self.env.get("LLM_MODEL", "")

    @property
    def mcp_url(self) -> str:
        return self.env.get("MCP_URL", "http://blender:8000/mcp")

    def python_for(self, key: str | None) -> Path:
        key = key or self.python_default
        if key not in self.python_envs:
            raise ConfigError(f"unknown python env {key!r}; available: {sorted(self.python_envs)}")
        venv = self.root / self.python_envs[key]
        exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not exe.is_file():
            raise ConfigError(f"python env {key} not installed at {venv}; run: python3 envinstall.py")
        return exe


def load_settings(root: Path) -> Settings:
    pj_path = root / "project.json"
    if not pj_path.is_file():
        raise ConfigError(f"project.json not found in {root}")
    try:
        pj = json.loads(pj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"project.json is not valid JSON: {exc}") from exc
    try:
        bench = pj["benchmark"]
        codex = pj.get("codex", {})
        scene = pj.get("scene", {})
        python = pj.get("python", {})
        return Settings(
            root=root,
            benchmark_name=bench["name"],
            benchmark_version=bench["version"],
            deps=pj.get("deps", {}),
            python_default=python.get("default", "3.12"),
            python_envs=python.get("envs", {"3.12": ".venv/py312", "3.14": ".venv/py314"}),
            codex_binary=codex.get("binary", "codex"),
            codex_sandbox=codex.get("sandbox", "workspace-write"),
            codex_extra_args=list(codex.get("extra_args", [])),
            scene_reset=bool(scene.get("reset", True)),
            scene_save_blend=bool(scene.get("save_blend", True)),
            default_limits={**DEFAULT_LIMITS, **pj.get("defaults", {}).get("limits", {})},
            projects_dir=root / pj.get("projects_dir", "projects"),
            outputs_dir=root / pj.get("outputs_dir", "outputs"),
            env=load_dotenv(root / ".env"),
        )
    except KeyError as exc:
        raise ConfigError(f"project.json missing key: {exc}") from exc


def codex_config_toml(settings: Settings) -> str:
    """~/.codex/config.toml content generated from .env (single source of truth)."""
    e = settings.env
    model = e.get("LLM_MODEL") or "gpt-5-codex"
    provider = e.get("LLM_PROVIDER") or "openai"
    lines = ["# Generated by controller.py from .env — do not edit by hand.",
             f'model = "{model}"', f'model_provider = "{provider}"', ""]
    if provider != "openai" or e.get("LLM_BASE_URL") not in (None, "", "https://api.openai.com/v1"):
        wire = e.get("LLM_WIRE_API") or ("responses" if provider == "openai" else "chat")
        lines += [f"[model_providers.{provider}]",
                  f'name = "{provider}"',
                  f'base_url = "{e.get("LLM_BASE_URL", "https://api.openai.com/v1")}"',
                  f'env_key = "{e.get("LLM_ENV_KEY", "OPENAI_API_KEY")}"',
                  f'wire_api = "{wire}"', ""]
    lines += ["[mcp_servers.blender]", f'url = "{settings.mcp_url}"', ""]
    return "\n".join(lines)


def write_codex_config(settings: Settings) -> Path:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(codex_config_toml(settings), encoding="utf-8")
    return path


def codex_env(settings: Settings) -> dict[str, str]:
    """Environment for the codex subprocess: the provider key under its env_key name."""
    env = dict(os.environ)
    api_key = settings.env.get("LLM_API_KEY") or ""
    env_key = settings.env.get("LLM_ENV_KEY") or "OPENAI_API_KEY"
    if api_key:
        env[env_key] = api_key
    return env


# ---------------------------------------------------------------------------
# Projects (tasks)
# ---------------------------------------------------------------------------
@dataclass
class Project:
    id: str
    dir: Path
    prompt: str
    python: str | None
    judge: Path
    judge_timeout: float
    limits: dict[str, Any]

    def resolved_limits(self, defaults: dict[str, Any]) -> dict[str, Any]:
        merged = {**defaults, **{k: v for k, v in self.limits.items()}}
        if merged.get("min_rounds") is None and merged.get("max_turns") is None \
                and merged.get("max_tokens") is None:
            raise ConfigError(
                f"{self.id}: limits are all unlimited — set at least one of "
                "min_rounds / max_turns / max_tokens")
        if merged.get("min_rounds") is not None and merged["min_rounds"] < 0:
            raise ConfigError(f"{self.id}: min_rounds must be >= 0 or null")
        return merged


def load_project(pdir: Path) -> Project:
    tj = pdir / "task.json"
    if not tj.is_file():
        raise ConfigError(f"{pdir}: missing task.json")
    data = json.loads(tj.read_text(encoding="utf-8"))
    pid = data.get("id") or pdir.name
    if not re.fullmatch(r"project_\d+", pid):
        raise ConfigError(f"{pdir}: id must look like project_<number>, got {pid!r}")
    if not data.get("prompt"):
        raise ConfigError(f"{pdir}: task.json needs a prompt")
    judge = pdir / data.get("judge", "judge.py")
    if not judge.is_file():
        raise ConfigError(f"{pdir}: judge script not found: {judge.name}")
    if not (pdir / "workspace").is_dir():
        raise ConfigError(f"{pdir}: missing workspace/ directory")
    return Project(id=pid, dir=pdir, prompt=data["prompt"], python=data.get("python"),
                   judge=judge, judge_timeout=float(data.get("judge_timeout", 600)),
                   limits=data.get("limits", {}))


def discover_projects(settings: Settings, only: list[str] | None = None) -> list[Project]:
    if not settings.projects_dir.is_dir():
        raise ConfigError(f"projects dir not found: {settings.projects_dir}")
    dirs = sorted((d for d in settings.projects_dir.iterdir()
                   if d.is_dir() and re.fullmatch(r"project_\d+", d.name)),
                  key=lambda d: int(d.name.rsplit("_", 1)[1]))
    projects = [load_project(d) for d in dirs]
    if only:
        wanted = set(only)
        unknown = wanted - {p.id for p in projects}
        if unknown:
            raise ConfigError(f"unknown project(s): {sorted(unknown)}")
        projects = [p for p in projects if p.id in wanted]
    if not projects:
        raise ConfigError("no projects found")
    return projects


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
    shutil.copy2(project.dir / "task.json", task_dir / "task.json")
    return task_dir




# ---------------------------------------------------------------------------
# Codex CLI wrapper (one invocation = one round; continuation via resume --last)
# ---------------------------------------------------------------------------
@dataclass
class RoundResult:
    round: int
    exit_code: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    error: str | None = None
    context_limited: bool = False
    final_message: str = ""
    seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def parse_codex_event(line: str) -> dict[str, Any]:
    """Extract what the controller needs from one codex --json event line.

    Returns any of: usage, mcp_call, error, agent_text.
    Unknown shapes are ignored (the raw line is always kept in the round log).
    """
    out: dict[str, Any] = {}
    try:
        ev = json.loads(line)
    except ValueError:
        return out
    if not isinstance(ev, dict):
        return out
    usage = ev.get("usage")
    if isinstance(usage, dict):
        out["usage"] = {
            "input": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "output": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
            "cached": int(usage.get("cached_input_tokens") or 0),
        }
    item = ev.get("item") if isinstance(ev.get("item"), dict) else ev
    if item.get("type") == "mcp_tool_call":
        out["mcp_call"] = {
            "server": item.get("server"), "tool": item.get("tool"),
            "arguments": item.get("arguments"),
            "status": item.get("status") or ("error" if item.get("error") else "ok"),
            "error": item.get("error"),
        }
    if ev.get("type") in ("error", "turn.failed") or item.get("type") == "error":
        out["error"] = str(ev.get("message") or item.get("message") or item.get("error") or ev)
    if item.get("type") == "agent_message" and item.get("text"):
        out["agent_text"] = item["text"]
    return out


class Audit:
    """Append-only controller-level event log (audit.jsonl)."""

    def __init__(self, path: Path) -> None:
        self._fh = open(path, "a", encoding="utf-8")
        self.t0 = time.monotonic()

    def event(self, kind: str, **data: Any) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"),
               "elapsed": round(time.monotonic() - self.t0, 3), "type": kind, **data}
        self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def run_codex_round(settings: Settings, workspace: Path, task_dir: Path, prompt: str,
                    round_no: int, audit: Audit) -> RoundResult:
    argv = [settings.codex_binary, "exec", "--json", "--skip-git-repo-check",
            "--sandbox", settings.codex_sandbox, *settings.codex_extra_args]
    if round_no > 1:
        argv += ["resume", "--last"]
    argv.append(prompt)
    log = task_dir / "rounds" / f"round_{round_no:03d}.jsonl"
    err_log = task_dir / "rounds" / f"round_{round_no:03d}.stderr.log"
    audit.event("round_start", round=round_no, argv=argv[:-1] + ["<prompt>"],
                prompt_length=len(prompt), resume=round_no > 1)
    t0 = time.monotonic()
    result = RoundResult(round=round_no, exit_code=-1)
    with open(log, "w", encoding="utf-8") as log_fh, \
            open(err_log, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(argv, cwd=workspace, env=codex_env(settings),
                                stdout=subprocess.PIPE, stderr=err_fh, text=True,
                                encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            log_fh.write(line + "\n")
            log_fh.flush()
            info = parse_codex_event(line)
            if "usage" in info:
                u = info["usage"]
                result.input_tokens += u["input"]
                result.output_tokens += u["output"]
                result.cached_tokens += u["cached"]
            if "mcp_call" in info:
                audit.event("mcp_call", round=round_no, **info["mcp_call"])
            if "error" in info:
                result.error = info["error"]
                audit.event("round_error", round=round_no, error=info["error"])
            if "agent_text" in info:
                result.final_message = info["agent_text"]
        result.exit_code = proc.wait()
    result.seconds = round(time.monotonic() - t0, 1)
    err_text = err_log.read_text(encoding="utf-8", errors="replace")[-4000:]
    blob = (result.error or "") + "\n" + err_text
    if CONTEXT_LIMIT_RE.search(blob):
        result.context_limited = True
    audit.event("round_end", round=round_no, exit_code=result.exit_code,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cached_tokens=result.cached_tokens, seconds=result.seconds,
                context_limited=result.context_limited, error=result.error)
    return result


# ---------------------------------------------------------------------------
# Blender scene management through BlenderMCP (reset before, save after)
# ---------------------------------------------------------------------------
SCENE_RESET_CODE = (
    "import bpy\n"
    "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
    "for _ in range(3):\n"
    "    bpy.data.orphans_purge(do_recursive=True)\n"
    "print('scene reset ok')"
)


async def _execute_blender_code(mcp_url: str, code: str, timeout: float) -> tuple[str, bool]:
    from datetime import timedelta

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("execute_blender_code", {"code": code},
                                          read_timeout_seconds=timedelta(seconds=timeout))
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            return text, bool(res.isError)


def scene_reset(settings: Settings, audit: Audit) -> None:
    """Wipe the Blender scene; failure raises (a dirty scene must fail the task)."""
    text, is_error = asyncio.run(_execute_blender_code(settings.mcp_url, SCENE_RESET_CODE, 300))
    audit.event("scene_reset", ok=not is_error, detail=text[:2000])
    if is_error:
        raise RuntimeError(f"Blender scene reset failed: {text[:500]}")


def scene_save(settings: Settings, task_dir: Path, audit: Audit) -> str | None:
    """Save the .blend work file into the task dir (best effort)."""
    target = (task_dir / "scene.blend").resolve()
    code = (f"import bpy\nbpy.ops.wm.save_as_mainfile(filepath={str(target)!r}, check_existing=False)\n"
            "print('saved', bpy.data.filepath)")
    try:
        text, is_error = asyncio.run(_execute_blender_code(settings.mcp_url, code, 300))
    except Exception as exc:
        audit.event("scene_saved", error=f"{type(exc).__name__}: {exc}")
        return None
    audit.event("scene_saved", ok=not is_error, path=str(target), detail=text[:2000])
    if is_error or not target.is_file():
        return None
    return str(target)


# ---------------------------------------------------------------------------
# Judge scripts
# ---------------------------------------------------------------------------
def run_judge(settings: Settings, project: Project, task_dir: Path, audit: Audit) -> dict[str, Any]:
    """Run projects/project_N/judge.py with the project's python environment.

    Contract: judge.py --workspace <dir> --run <dir> --out <score.json>;
    exit 0 and a score.json with {score: 0-100, passed: bool, details: {...}}.
    """
    judge_dir = task_dir / "judge"
    out_file = judge_dir / "score.json"
    try:
        py = settings.python_for(project.python)
    except ConfigError as exc:
        audit.event("judge_error", error=str(exc))
        return {"status": "judge_error", "error": str(exc)}
    env = dict(os.environ)
    env["PYTHONPATH"] = str(settings.root) + os.pathsep + env.get("PYTHONPATH", "")
    env["MCP_URL"] = settings.mcp_url
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
        assert isinstance(score.get("score"), (int, float)) and 0 <= score["score"] <= 100
        assert isinstance(score.get("passed"), bool)
    except Exception as exc:
        audit.event("judge_error", error=f"invalid score.json: {exc}")
        return {"status": "judge_error", "error": f"invalid score.json: {exc}", "seconds": seconds}
    score.update(status="judged", seconds=seconds)
    audit.event("judge_end", score=score["score"], passed=score["passed"], seconds=seconds)
    return score


# ---------------------------------------------------------------------------
# Task loop (serial; one task = copy workspace -> agent rounds -> judge)
# ---------------------------------------------------------------------------
def agent_phase(settings: Settings, project: Project, task_dir: Path,
                audit: Audit) -> dict[str, Any]:
    """Drive codex until the agent is done or a limit hits (limits are AND-ed:
    the first one reached ends the conversation; each may be null = unlimited)."""
    limits = project.resolved_limits(settings.default_limits)
    workspace = task_dir / "workspace"
    rounds, tokens = 0, 0
    status, error, final_message = "completed", None, ""
    while True:
        if limits["max_turns"] is not None and rounds >= limits["max_turns"]:
            status = "max_turns"
            break
        prompt = project.prompt if rounds == 0 else NAG_PROMPT
        r = run_codex_round(settings, workspace, task_dir, prompt, rounds + 1, audit)
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


def run_task(settings: Settings, project: Project, run_dir: Path) -> dict[str, Any]:
    task_dir = prepare_task_dir(project, run_dir)
    audit = Audit(task_dir / "audit.jsonl")
    t0 = time.monotonic()
    result: dict[str, Any] = {"id": project.id, "status": "pending"}
    audit.event("task_start", id=project.id, prompt_length=len(project.prompt))
    try:
        if settings.scene_reset:
            try:
                scene_reset(settings, audit)
            except Exception as exc:
                result.update(status="scene_error", error=str(exc))
                audit.event("task_end", **{k: v for k, v in result.items() if k != "id"})
                return _finish_task(result, task_dir, audit, t0)
        agent = agent_phase(settings, project, task_dir, audit)
        result.update(agent)
        if settings.scene_save_blend:
            saved = scene_save(settings, task_dir, audit)
            if saved:
                result["scene_blend"] = "scene.blend"
    except KeyboardInterrupt:
        result.update(status="interrupted")
        audit.event("task_end", status="interrupted")
        _finish_task(result, task_dir, audit, t0)
        raise
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
        audit.event("task_error", error=result["error"])
    judge = run_judge(settings, project, task_dir, audit)
    result["judge"] = judge
    if judge.get("status") == "judged":
        result["score"] = judge["score"]
        result["passed"] = judge["passed"]
    return _finish_task(result, task_dir, audit, t0)


def _finish_task(result: dict[str, Any], task_dir: Path, audit: Audit, t0: float) -> dict[str, Any]:
    result["answer_seconds"] = round(time.monotonic() - t0, 1)
    (task_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    audit.event("task_end", status=result["status"], answer_seconds=result["answer_seconds"],
                score=result.get("score"))
    audit.close()
    return result


def masked_env(env: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if re.search(r"KEY|TOKEN|SECRET", k) else v) for k, v in env.items()}


def git_rev(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.root)
    projects = discover_projects(settings, args.project)
    write_codex_config(settings)
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
        results.append(run_task(settings, p, run_dir))
        write_summary(settings, run_dir, results, started)
    write_summary(settings, run_dir, results, started)
    print((run_dir / "summary.md").read_text(encoding="utf-8"))
    ok = all(r["status"] == "completed" for r in results)
    return 0 if ok else 1


def write_summary(settings: Settings, run_dir: Path, results: list[dict[str, Any]],
                  started: datetime) -> None:
    data = {"benchmark": {"name": settings.benchmark_name, "version": settings.benchmark_version},
            "model": settings.model, "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"), "tasks": results}
    scores = [r["score"] for r in results if r.get("score") is not None]
    data["mean_score"] = sum(scores) / len(scores) if scores else None
    run_json = run_dir / "run.json"
    snap = json.loads(run_json.read_text(encoding="utf-8"))
    snap["finished"] = data["finished"]
    snap["mean_score"] = data["mean_score"]
    run_json.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    fmt = lambda v: "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))  # noqa: E731
    lines = [f"# {settings.benchmark_name} v{settings.benchmark_version}", "",
             f"model `{settings.model}`, started {data['started']}", "",
             "| project | status | score | rounds | tokens | answer time |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['status']} | {fmt(r.get('score'))} | {r.get('rounds', '-')} | "
                     f"{r.get('tokens', '-')} | {r.get('answer_seconds', '-')}s |")
    lines += ["", f"Mean score: {fmt(data['mean_score'])} over {len(scores)} judged project(s)."]
    for r in results:
        if r.get("error"):
            lines.append(f"\n- `{r['id']}`: {r['error']}")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# check / envinfo / build
# ---------------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    settings = load_settings(args.root)
    ok = True
    print(f"benchmark: {settings.benchmark_name} v{settings.benchmark_version}")
    print(f"deps: {settings.deps}")
    print(f"model: {settings.model or '(LLM_MODEL not set)'}")
    if not (settings.env.get("LLM_API_KEY") or settings.env.get(settings.env.get("LLM_ENV_KEY", "OPENAI_API_KEY"))):
        print("WARNING: no API key set (.env LLM_API_KEY or the provider env_key)")
    for p in discover_projects(settings, args.project):
        try:
            limits = p.resolved_limits(settings.default_limits)
            print(f"[{p.id}] judge={p.judge.name} python={p.python or settings.python_default} "
                  f"limits={limits}")
            settings.python_for(p.python)
        except ConfigError as exc:
            print(f"[{p.id}] ERROR: {exc}")
            ok = False
    if shutil.which(settings.codex_binary) or Path(settings.codex_binary).is_file():
        print(f"codex: {settings.codex_binary}")
    else:
        print(f"WARNING: codex binary not found: {settings.codex_binary}")
    cfg = codex_config_toml(settings)
    print("--- generated codex config.toml ---")
    print(cfg)
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
    """Build docker images with the versions pinned in project.json."""
    settings = load_settings(args.root)
    env = dict(os.environ)
    env.update({"BLENDER_VERSION": settings.deps["blender"],
                "BLENDERMCP_VERSION": settings.deps["blendermcp"],
                "CODEX_VERSION": settings.deps["codexcli"]})
    cmd = ["docker", "compose", "build"] + list(args.args or [])
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=settings.root, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="controller.py", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--project", action="append", help="only this project (repeatable)")
    sub.add_parser("envinfo")
    sp = sub.add_parser("build", help="docker compose build with project.json versions")
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
