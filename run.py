#!/usr/bin/env python3
"""headless-3d-bench entry point.

  python run.py run       # run all projects (serial)
  python run.py check     # validate config and projects
  python run.py envinfo   # show python environments
  python run.py build     # docker compose build with project.json versions
"""
from core.controller import main

if __name__ == "__main__":
    raise SystemExit(main())
