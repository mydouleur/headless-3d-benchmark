"""Silhouette comparison between rendered views (numpy + Pillow only)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_mask(path: Path) -> np.ndarray:
    """Foreground mask from a transparent-background render."""
    a = np.asarray(Image.open(path).convert("RGBA"))
    return a[..., 3] > 10


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
        scores[v] = iou(load_mask(r), load_mask(c))
    return scores
