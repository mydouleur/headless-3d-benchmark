"""Silhouette comparison between rendered views (numpy + Pillow only)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_mask(path: Path, require_foreground: bool = False) -> np.ndarray:
    """Foreground mask from a transparent-background render.

    require_foreground=True raises when the image has no alpha channel or an
    empty silhouette — a broken render must not silently score."""
    img = Image.open(path)
    if require_foreground and "A" not in img.getbands():
        raise ValueError(f"{path.name}: render has no alpha channel (expected transparent PNG)")
    a = np.asarray(img.convert("RGBA"))
    mask = a[..., 3] > 10
    if require_foreground and not mask.any():
        raise ValueError(f"{path.name}: empty silhouette (nothing rendered?)")
    return mask


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0  # both empty
    return float(inter / union)


def compare_dirs(reference: Path, candidate: Path, views: tuple[str, ...]) -> dict[str, float]:
    """Per-view silhouette IoU between two folders of <view>.png renders."""
    scores: dict[str, float] = {}
    for v in views:
        r, c = reference / f"{v}.png", candidate / f"{v}.png"
        if not r.is_file() or not c.is_file():
            raise FileNotFoundError(f"missing render for view {v!r} (ref={r.is_file()}, cand={c.is_file()})")
        scores[v] = iou(load_mask(r, require_foreground=True), load_mask(c))
    return scores
