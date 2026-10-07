"""Red-team: real-repo consistency — the shipped projects.json must not
misdescribe the actual workspace inputs (prompt vs refs/ contents).

Runs against the REAL repo (not a tmp fixture). A failure here means the
prompt the agent reads disagrees with the files it actually gets.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# canonical view names a prompt might claim
VIEW_NAMES = ("front", "back", "left", "right", "top", "bottom", "iso", "side")


def test_r34_prompt_matches_actual_refs():
    """Every enabled project's prompt must claim exactly the ref views that
    exist in workspace/refs/ — no missing files promised, no shipped files
    left unmentioned."""
    projects = json.loads((REPO_ROOT / "projects.json").read_text(encoding="utf-8"))["projects"]
    problems = []
    for entry in projects:
        if entry.get("enabled", True) is False:
            continue
        refs = REPO_ROOT / "projects" / entry["id"] / "workspace" / "refs"
        if not refs.is_dir():
            continue
        actual = {p.stem for p in refs.iterdir() if p.is_file()}
        prompt = entry["prompt"]
        claimed = {v for v in VIEW_NAMES if re.search(rf"\b{re.escape(v)}\b", prompt)}
        missing = claimed - actual          # promised but not shipped
        unmentioned = actual - claimed      # shipped but not described
        if missing or unmentioned:
            problems.append(f"{entry['id']}: prompt claims {sorted(missing)} "
                            f"not in refs/, refs/ has {sorted(unmentioned)} not in prompt")
    assert not problems, "; ".join(problems)
