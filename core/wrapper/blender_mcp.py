"""Blender scene management through BlenderMCP (streamable HTTP).

reset() before each task guarantees scene isolation; save() keeps the .blend
work file in the task directory as audit evidence.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from ..audit import Audit
from ..settings import Settings

SCENE_RESET_CODE = (
    "import bpy\n"
    "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
    "for _ in range(3):\n"
    "    bpy.data.orphans_purge(do_recursive=True)\n"
    "print('scene reset ok')"
)


async def execute_blender_code(mcp_url: str, code: str, timeout: float = 300) -> tuple[str, bool]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("execute_blender_code", {"code": code},
                                          read_timeout_seconds=timedelta(seconds=timeout))
            text = "\n".join(getattr(c, "text", "") for c in res.content)
            return text, bool(res.isError)


class BlenderMcpScene:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def reset(self, audit: Audit) -> None:
        text, is_error = asyncio.run(execute_blender_code(self.settings.mcp_url, SCENE_RESET_CODE))
        audit.event("scene_reset", ok=not is_error, detail=text[:2000])
        if is_error:
            raise RuntimeError(f"Blender scene reset failed: {text[:500]}")

    def save(self, task_dir: Path, audit: Audit) -> str | None:
        target = (task_dir / "scene.blend").resolve()
        code = (f"import bpy\nbpy.ops.wm.save_as_mainfile(filepath={str(target)!r}, check_existing=False)\n"
                "print('saved', bpy.data.filepath)")
        try:
            text, is_error = asyncio.run(execute_blender_code(self.settings.mcp_url, code))
        except Exception as exc:
            audit.event("scene_saved", error=f"{type(exc).__name__}: {exc}")
            return None
        audit.event("scene_saved", ok=not is_error, path=str(target), detail=text[:2000])
        if is_error or not target.is_file():
            return None
        return str(target)
