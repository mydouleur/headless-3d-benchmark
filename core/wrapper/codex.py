"""Codex CLI wrapper: `codex exec --json` rounds, `resume --last` continuation.

The raw --json event stream is kept per round under task_dir/rounds/; token
usage, MCP tool calls, command executions, file changes, reasoning text and
screenshots are extracted for the audit log and the per-round markdown.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audit import Audit
from ..interface.agent import RoundResult
from ..settings import ConfigError, Settings

CONTEXT_LIMIT_RE = re.compile(
    r"context[_ ]?(length|window|limit)|maximum context|too many tokens|context overflow",
    re.IGNORECASE)

KNOWN_USAGE_KEYS = {"input_tokens", "output_tokens", "cached_input_tokens",
                    "prompt_tokens", "completion_tokens"}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _toml_str(value: str, field_name: str) -> str:
    """A TOML basic string, rejecting values that could inject extra config."""
    if '"' in value or "\\" in value or "\n" in value or "\r" in value:
        raise ConfigError(f".env {field_name}: must not contain quotes, backslashes or newlines")
    return f'"{value}"'


def _find_images(node: Any, out: list[tuple[str, str]]) -> None:
    """Recursively collect base64 image blocks ({type: image, data, mimeType})."""
    if isinstance(node, dict):
        if node.get("type") == "image" and isinstance(node.get("data"), str):
            out.append((node["data"], node.get("mimeType") or node.get("mime_type") or "image/png"))
            return
        for v in node.values():
            _find_images(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_images(v, out)


def parse_codex_event(line: str) -> dict[str, Any]:
    """Extract what the controller needs from one codex --json event line.

    Returns any of: usage, usage_unknown, mcp_call, command, file_change,
    reasoning, error, agent_text, images. Unknown shapes are ignored (the raw
    line is always kept in the round log); dirty fields degrade to 0/None.
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
            "input": _safe_int(usage.get("input_tokens") or usage.get("prompt_tokens")),
            "output": _safe_int(usage.get("output_tokens") or usage.get("completion_tokens")),
            "cached": _safe_int(usage.get("cached_input_tokens")),
        }
        unknown = set(usage) - KNOWN_USAGE_KEYS
        if unknown:
            out["usage_unknown"] = sorted(unknown)
    item = ev.get("item") if isinstance(ev.get("item"), dict) else ev
    itype = item.get("type")
    if itype == "mcp_tool_call":
        out["mcp_call"] = {
            "server": item.get("server"), "tool": item.get("tool"),
            "arguments": item.get("arguments"),
            "status": item.get("status") or ("error" if item.get("error") else "ok"),
            "error": item.get("error"),
        }
    elif itype == "command_execution":
        out["command"] = {"command": item.get("command"), "exit_code": item.get("exit_code"),
                          "output": item.get("aggregated_output") or item.get("output")}
    elif itype == "file_change":
        out["file_change"] = item.get("changes") or item.get("paths") or item
    elif itype == "reasoning":
        text = item.get("text") or item.get("summary")
        if isinstance(text, list):
            text = "\n".join(str(t.get("text", t)) if isinstance(t, dict) else str(t) for t in text)
        if text:
            out["reasoning"] = str(text)
    if ev.get("type") in ("error", "turn.failed") or itype == "error":
        msg = ev.get("message") or item.get("message") or item.get("error")
        # never dump the whole event (it may embed base64 screenshots)
        out["error"] = str(msg)[:2000] if msg is not None else str(ev.get("type") or "error")
    if itype == "agent_message" and item.get("text"):
        out["agent_text"] = item["text"]
    images: list[tuple[str, str]] = []
    _find_images(ev, images)
    if images:
        out["images"] = images
    return out


class CodexWrapper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- configuration -------------------------------------------------------
    def config_toml(self) -> str:
        """~/.codex/config.toml content generated from .env (single source of truth)."""
        e = self.settings.env
        model = e.get("LLM_MODEL") or "gpt-5-codex"
        provider = e.get("LLM_PROVIDER") or "openai"
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", provider):
            # a dotted name silently nests [model_providers.open.router]; "]" breaks TOML
            raise ConfigError(f"LLM_PROVIDER must be alphanumeric plus _ and -, got {provider!r}")
        lines = ["# Generated by core/wrapper/codex.py from .env — do not edit by hand.",
                 f'model = {_toml_str(model, "LLM_MODEL")}',
                 f'model_provider = {_toml_str(provider, "LLM_PROVIDER")}', ""]
        if provider != "openai" or e.get("LLM_BASE_URL") not in (None, "", "https://api.openai.com/v1"):
            wire = e.get("LLM_WIRE_API") or ("responses" if provider == "openai" else "chat")
            lines += [f"[model_providers.{provider}]",
                      f'name = {_toml_str(provider, "LLM_PROVIDER")}',
                      f'base_url = {_toml_str(e.get("LLM_BASE_URL", "https://api.openai.com/v1"), "LLM_BASE_URL")}',
                      f'env_key = {_toml_str(e.get("LLM_ENV_KEY", "OPENAI_API_KEY"), "LLM_ENV_KEY")}',
                      f'wire_api = {_toml_str(wire, "LLM_WIRE_API")}', ""]
        lines += ["[mcp_servers.blender]", f'url = {_toml_str(self.settings.mcp_url, "MCP_URL")}', ""]
        toml = "\n".join(lines)
        import tomllib  # self-check: the generated config must parse
        tomllib.loads(toml)
        return toml

    def write_config(self) -> None:
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        if self._drop_privileges():
            home = Path(f"/home/{self.settings.codex_user}/.codex")
        home.mkdir(parents=True, exist_ok=True)
        target = home / "config.toml"
        if target.is_file() and target.read_text(encoding="utf-8") != self.config_toml():
            backup = target.with_suffix(".toml.bak")
            backup.write_bytes(target.read_bytes())  # never silently clobber a user's config
        target.write_text(self.config_toml(), encoding="utf-8")
        if self._drop_privileges():
            import pwd
            import grp
            uid = pwd.getpwnam(self.settings.codex_user).pw_uid
            gid = grp.getgrnam(self.settings.codex_user).gr_gid
            for p in (home, target):
                os.chown(p, uid, gid)

    def _drop_privileges(self) -> bool:
        """Run the agent CLI as an unprivileged user when we are root on Linux."""
        return (bool(self.settings.codex_user) and os.name == "posix"
                and hasattr(os, "geteuid") and os.geteuid() == 0)

    def _env(self) -> dict[str, str]:
        """The provider API key exported under the env_key codex expects."""
        env = dict(os.environ)
        env_key = self.settings.env.get("LLM_ENV_KEY") or "OPENAI_API_KEY"
        api_key = self.settings.env.get("LLM_API_KEY") or self.settings.env.get(env_key) or ""
        if api_key:
            env[env_key] = api_key
        return env

    # -- one round -------------------------------------------------------------
    def run_round(self, workspace: Path, task_dir: Path, prompt: str, round_no: int,
                  audit: Audit) -> RoundResult:
        s = self.settings
        # --approve-for-me routes approvals through automatic review using the
        # workspace-write sandbox; codex rejects combining it with --sandbox.
        argv = [s.codex_binary, *s.codex_extra_args,
                "exec", "--json", "--skip-git-repo-check", "--approve-for-me"]
        if round_no > 1:
            argv += ["resume", "--last"]
        argv.append(prompt)
        env = self._env()
        if self._drop_privileges():
            # root launches codex as the unprivileged agent user: projects/
            # (judge scripts, answers) stay root-owned and unreadable to it
            user = s.codex_user
            argv = ["setpriv", f"--reuid={user}", f"--regid={user}", "--clear-groups"] + argv
            env["HOME"] = f"/home/{user}"
            env["CODEX_HOME"] = f"/home/{user}/.codex"
        round_dir = task_dir / "rounds"
        log = round_dir / f"round_{round_no:03d}.jsonl"
        err_log = round_dir / f"round_{round_no:03d}.stderr.log"
        audit.event("round_start", round=round_no, argv=argv[:-1] + ["<prompt>"],
                    prompt_length=len(prompt), resume=round_no > 1,
                    user=s.codex_user if self._drop_privileges() else None)
        t0 = time.monotonic()
        result = RoundResult(round=round_no, exit_code=-1)
        # per-round evidence collectors
        reasoning: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        file_changes: list[Any] = []
        images_saved: list[str] = []
        images_dir = round_dir / f"round_{round_no:03d}_images"
        with open(log, "w", encoding="utf-8") as log_fh, \
                open(err_log, "w", encoding="utf-8") as err_fh:
            proc = subprocess.Popen(argv, cwd=workspace, env=env,
                                    stdout=subprocess.PIPE, stderr=err_fh, text=True,
                                    encoding="utf-8", errors="replace")
            try:
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
                    if "usage_unknown" in info:
                        audit.event("usage_unknown_keys", round=round_no, keys=info["usage_unknown"])
                    if "mcp_call" in info:
                        tool_calls.append({"kind": "mcp", **info["mcp_call"]})
                        audit.event("mcp_call", round=round_no, **info["mcp_call"])
                    if "command" in info:
                        tool_calls.append({"kind": "command", **info["command"]})
                        audit.event("command_execution", round=round_no,
                                    command=info["command"].get("command"),
                                    exit_code=info["command"].get("exit_code"))
                    if "file_change" in info:
                        file_changes.append(info["file_change"])
                        audit.event("file_change", round=round_no, changes=info["file_change"])
                    if "reasoning" in info:
                        reasoning.append(info["reasoning"])
                    if "images" in info:
                        for data_b64, mime in info["images"]:
                            try:
                                payload = base64.b64decode(data_b64, validate=True)
                            except (binascii.Error, ValueError):
                                audit.event("image_dropped", round=round_no,
                                            reason="invalid base64")
                                continue
                            images_dir.mkdir(exist_ok=True)
                            ext = {"image/jpeg": ".jpg", "image/webp": ".webp",
                                   "image/gif": ".gif"}.get(mime, ".png")
                            path = images_dir / f"{len(images_saved) + 1:03d}{ext}"
                            path.write_bytes(payload)
                            images_saved.append(str(path.relative_to(task_dir)))
                        if images_saved:
                            audit.event("images_saved", round=round_no, paths=images_saved)
                    if "error" in info:
                        result.error = info["error"]
                        audit.event("round_error", round=round_no, error=info["error"])
                    if "agent_text" in info:
                        result.final_message = info["agent_text"]
            except BaseException:
                # never leave the agent CLI running as an orphan
                proc.kill()
                proc.wait()
                raise
            result.exit_code = proc.wait()
        result.seconds = round(time.monotonic() - t0, 1)
        err_text = err_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        if CONTEXT_LIMIT_RE.search((result.error or "") + "\n" + err_text):
            result.context_limited = True
        self._write_round_md(round_dir / f"round_{round_no:03d}.md", prompt, result,
                             reasoning, tool_calls, file_changes, images_saved)
        audit.event("round_end", round=round_no, exit_code=result.exit_code,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cached_tokens=result.cached_tokens, seconds=result.seconds,
                    tool_calls=len(tool_calls), file_changes=len(file_changes),
                    images=len(images_saved),
                    context_limited=result.context_limited, error=result.error)
        return result

    @staticmethod
    def _write_round_md(path: Path, prompt: str, result: RoundResult,
                        reasoning: list[str], tool_calls: list[dict[str, Any]],
                        file_changes: list[Any], images: list[str]) -> None:
        """Human-readable per-round record: prompt, thinking, tools, files, images."""
        def clip(text: Any, n: int = 2000) -> str:
            text = str(text)
            return text if len(text) <= n else text[:n] + f" …(+{len(text) - n} chars)"

        lines = [f"# Round {result.round}", "",
                 f"exit {result.exit_code}, {result.seconds}s, "
                 f"tokens in/out/cached: {result.input_tokens}/{result.output_tokens}/{result.cached_tokens}",
                 "", "## Prompt", "", "```text", clip(prompt, 4000), "```"]
        if reasoning:
            lines += ["", "## Thinking"]
            for r in reasoning:
                lines += ["", "```text", clip(r), "```"]
        if tool_calls:
            lines += ["", "## Tool calls"]
            for tc in tool_calls:
                if tc["kind"] == "mcp":
                    lines.append(f"- MCP `{tc.get('server')}.{tc.get('tool')}` "
                                 f"args={clip(tc.get('arguments'), 300)} status={tc.get('status')}")
                else:
                    lines.append(f"- command `{clip(tc.get('command'), 200)}` exit={tc.get('exit_code')}")
        if file_changes:
            lines += ["", "## File changes"]
            for fc in file_changes:
                lines.append(f"- {clip(fc, 300)}")
        if images:
            lines += ["", "## Screenshots / images"]
            for img in images:
                lines.append(f"- ![{img}]({img.replace('rounds/', '')})" if img.startswith("rounds/")
                             else f"- {img}")
        if result.final_message:
            lines += ["", "## Agent message", "", clip(result.final_message, 4000)]
        if result.error:
            lines += ["", "## Error", "", clip(result.error)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
