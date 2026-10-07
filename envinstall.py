#!/usr/bin/env python3
"""Install the project environments into .venv/ (Linux/macOS).

Steps:
  1. install uv (static binary, via the official install script) if missing
  2. uv python install 3.12.10 / 3.14.8
  3. uv venv .venv/py312 (python 3.12.10) and .venv/py314 (python 3.14.8)
  4. uv pip install requirements-3.12.txt / requirements-3.14.txt into each

Usage:
  python3 envinstall.py              # install everything
  python3 envinstall.py --only 3.12  # just one environment

.venv/ is git-ignored; environments are rebuilt by this script or baked into
the Docker image (same commands).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# python version key -> (exact interpreter version, venv dir, requirements file)
ENVS = {
    "3.12": ("3.12.10", ".venv/py312", "requirements-3.12.txt"),
    "3.14": ("3.14.8", ".venv/py314", "requirements-3.14.txt"),
}


def run(cmd: list[str] | str, shell: bool = False) -> None:
    print("+", cmd if isinstance(cmd, str) else " ".join(cmd), flush=True)
    subprocess.run(cmd, shell=shell, check=True, cwd=ROOT)


def ensure_uv() -> str:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if Path(uv).is_file() or shutil.which("uv"):
        return uv
    if os.name == "nt":
        sys.exit("envinstall.py targets Linux/macOS. On Windows use WSL2, or install uv manually.")
    run("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True)
    if not Path(uv).is_file():
        sys.exit("uv installation failed; install it manually: https://docs.astral.sh/uv/")
    return uv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=list(ENVS), help="install only this python version")
    args = ap.parse_args()

    uv = ensure_uv()
    wanted = [args.only] if args.only else list(ENVS)
    for key in wanted:
        version, venv, req = ENVS[key]
        run([uv, "python", "install", version])
        run([uv, "venv", venv, "--python", version])
        run([uv, "pip", "install", "--python", str(Path(venv) / "bin" / "python"), "-r", req])
        run([str(Path(venv) / "bin" / "python"), "-c",
             f"import sys; print('env {key} ready:', sys.version)"])
    print("done. environments:")
    for key, (_, venv, _) in ENVS.items():
        print(f"  python {key}: {venv}/bin/python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
