#!/usr/bin/env python3
"""Judge for project_1 (mug).

Renders the candidate model and the hidden reference with the same headless
Blender (via BlenderMCP), then compares the six orthographic silhouettes (IoU).

Contract (all judge scripts):
  python project_N.py --workspace <dir> --run <dir> --out <score.json>
  exit 0 and write score.json = {"score": 0-100, "passed": bool, "details": {...}}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.utils import blender_render, compare

PASS_THRESHOLD = 60.0


def write(out_path: Path, score: float, passed: bool, details: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"score": round(score, 2), "passed": passed,
                                    "details": details}, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    model = args.workspace / "model.glb"
    if not model.is_file():
        write(args.out, 0.0, False, {"error": "workspace/model.glb not found"})
        return 0

    renders = args.run / "judge" / "renders"
    try:
        blender_render.render_views(model, renders / "candidate")
        blender_render.render_views(Path(__file__).parent / "reference" / "mug.glb",
                                    renders / "reference")
        per_view = compare.compare_dirs(renders / "reference", renders / "candidate",
                                        blender_render.VIEWS)
    except Exception as exc:
        write(args.out, 0.0, False, {"error": f"{type(exc).__name__}: {exc}"})
        return 0

    score = 100.0 * sum(per_view.values()) / len(per_view)
    write(args.out, score, score >= PASS_THRESHOLD,
          {"iou_per_view": {k: round(v, 4) for k, v in per_view.items()},
           "pass_threshold": PASS_THRESHOLD,
           "renders": str(renders.relative_to(args.run))})
    print(f"project_1 score: {score:.2f} (pass >= {PASS_THRESHOLD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
