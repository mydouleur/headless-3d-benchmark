"""Utilities shared by judge scripts (importable via PYTHONPATH=repo root).

blender_render — render six orthographic views of a model with the headless
                 Blender container through BlenderMCP (real Blender rendering)
compare        — silhouette IoU between rendered views
"""
from . import blender_render, compare

__all__ = ["blender_render", "compare"]
