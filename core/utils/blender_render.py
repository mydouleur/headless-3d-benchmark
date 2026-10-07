"""Render six orthographic views of a glb with the headless Blender container.

Goes through BlenderMCP's execute_blender_code, so judging uses the exact same
renderer the agent used — no local Blender install needed.

Paths: inside docker compose both containers mount ./outputs at /app/outputs,
so paths are identical. On a host setup set H3D_MCP_OUTPUTS_PREFIX to the
server-side mount point of the outputs directory (the controller exports
H3D_OUTPUTS_DIR / H3D_MCP_OUTPUTS_PREFIX to the judge environment).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

VIEWS = ("front", "back", "left", "right", "top", "bottom")

RENDER_SCRIPT = """
import bpy
from mathutils import Vector

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath={glb!r})

mins = Vector((1e18, 1e18, 1e18)); maxs = Vector((-1e18, -1e18, -1e18))
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH':
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            mins = Vector(map(min, mins, w)); maxs = Vector(map(max, maxs, w))
if mins.x > maxs.x:
    raise RuntimeError('no mesh objects in the scene')
center = (mins + maxs) / 2
size = max(max((maxs - mins)), 1e-6)

scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'FLAT'
scene.display.shading.color_type = 'MATERIAL'
scene.render.resolution_x = scene.render.resolution_y = {pixels}
scene.render.film_transparent = True
scene.render.image_settings.file_format = 'PNG'

cam_data = bpy.data.cameras.new('cam')
cam_data.type = 'ORTHO'
cam_data.ortho_scale = size * 1.3
cam_data.clip_start = size * 0.001
cam_data.clip_end = size * 20
cam = bpy.data.objects.new('cam', cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

views = {{'front': (0, -1, 0), 'back': (0, 1, 0), 'left': (-1, 0, 0),
          'right': (1, 0, 0), 'top': (0, 0, 1), 'bottom': (0, 0, -1)}}
for name, d in views.items():
    cam.location = center + Vector(d) * (size * 3)
    cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = {out!r} + '/' + name + '.png'
    bpy.ops.render.render(write_still=True)
print('rendered', len(views), 'views of', {glb!r})
"""


def mcp_path(path: Path) -> str:
    """Translate a local path under the outputs dir into the server's mount."""
    p = str(path.resolve())
    prefix = os.environ.get("H3D_MCP_OUTPUTS_PREFIX")
    base = os.environ.get("H3D_OUTPUTS_DIR")
    if prefix and base:
        base_r = str(Path(base).resolve())
        if p.startswith(base_r):
            return prefix.rstrip("/") + p[len(base_r):].replace("\\", "/")
    return p


async def _render(glb: Path, out_dir: Path, pixels: int) -> str:
    from core.wrapper.blender_mcp import execute_blender_code

    url = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
    out_dir.mkdir(parents=True, exist_ok=True)
    script = RENDER_SCRIPT.format(glb=mcp_path(glb), out=mcp_path(out_dir), pixels=pixels)
    text, is_error = await execute_blender_code(url, script, timeout=600)
    if is_error:
        raise RuntimeError(f"Blender render failed: {text[:800]}")
    return text


def render_views(glb: Path, out_dir: Path, pixels: int = 512) -> list[Path]:
    """Render VIEWS of ``glb`` into ``out_dir``; returns the png paths.

    The Blender container only sees the shared outputs mount, so a model that
    lives elsewhere (e.g. projects/<id>/reference/) is copied into out_dir
    first."""
    glb = Path(glb)
    outputs = os.environ.get("H3D_OUTPUTS_DIR")
    if outputs and not glb.resolve().is_relative_to(Path(outputs).resolve()):
        out_dir.mkdir(parents=True, exist_ok=True)
        staged = out_dir / "_input.glb"
        staged.write_bytes(glb.read_bytes())
        glb = staged
    asyncio.run(_render(glb, out_dir, pixels))
    paths = [out_dir / f"{v}.png" for v in VIEWS]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise RuntimeError(f"render did not produce: {missing}")
    return paths
