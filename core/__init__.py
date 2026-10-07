"""headless-3d-bench core package.

Layout:
  core/settings.py       config.json + .env loading (Settings)
  core/projects.py       project discovery (projects.json -> projects/<id>/)
  core/audit.py          append-only audit log
  core/controller.py     master orchestration + CLI
  core/interface/        contracts: AgentRunner / SceneManager / Judge
  core/wrapper/          implementations: codex / blender_mcp / python_judge
"""
