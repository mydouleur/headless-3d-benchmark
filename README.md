# headless-3d-bench

无头 3D 建模 agent benchmark:**Codex CLI 通过 BlenderMCP 指挥无头 Blender 建模,Python 总控驱动全部流程,每题由独立判题脚本评分,全程可审计。**

目标平台:纯 Linux Docker(Windows 开发请用 WSL2 + Docker)。

## 工作流程

```
config.json + .env          配置(版本、模型、API、默认 limits、追问话术)
      │
projects.json                题目列表(prompt/limits/python/enabled),按 id 索引 projects/<id>/
projects/project_N/          题目目录:workspace/(初始输入)+ reference/(判题答案,agent 不可见)+ project_N.py(判题)
      │
      ▼  python run.py run
outputs/<开始时间>_<模型>_v<benchmark版本>/project_N/
      ├── workspace/         题目副本,Codex 的工作目录(输入 + 产物 model.glb)
      ├── rounds/            每轮:codex --json 原始事件流 + stderr + round_NNN.md(可读记录:
      │                        提示词、思考、工具/MCP 调用、文件改动)+ round_NNN_images/(截图证据)
      ├── audit.jsonl        总控事件:轮次、token、MCP 调用、限制触发、判题
      ├── scene.blend        题目结束时的 Blender 场景(留痕)
      ├── judge/             判题 score.json + stdout/stderr + 渲染对比图
      └── result.json        状态、轮数、token、答题时间、分数
```

每题串行:重置 Blender 场景 → Codex 轮次循环(`codex exec`,续轮 `resume --last`)→ 保存 scene.blend → 判题。题目之间不共享场景。

## 快速开始

```bash
# 1. 配置 API(二选一:官方 OpenAI 或 OpenRouter 等兼容网关)
cp .env.example .env && $EDITOR .env

# 2. 构建镜像(版本号从 config.json 读)
python3 run.py build            # 需要 python3 可用;或直接 docker compose build

# 3. 跑 benchmark(controller 容器 + blender 容器自动编排)
docker compose run --rm controller python run.py run

# 只跑某题 / 校验配置 / 跑测试
docker compose run --rm controller python run.py run --project project_1
docker compose run --rm controller python run.py check
docker compose run --rm --no-deps controller        # pytest(不拉起 blender)
```

## 配置

- **`.env`** —— API 与 URL(LLM provider/key/model、MCP 端点等),逐字段注释见根目录 [`.env.example`](.env.example);总控据此生成 `~/.codex/config.toml`。
- **`config.json`** —— 总配置:benchmark 总版本号、deps 组件版本、判题 Python 环境、codex 参数、场景管理、全局默认 limits 与 nag_prompt。逐字段参考(含完整示例和"不写时如何生效"):[docs/config_ref.md](docs/config_ref.md)。
- **`projects.json`** —— 题目列表:benchmark 制作者增删题目只改这里;每题按 `id` 索引 `projects/<id>/` 目录。逐字段参考(含 limits 的 AND 语义、题目状态、判题接口):[docs/projects_ref.md](docs/projects_ref.md)。

## 给两类使用者

**Benchmark 制作者(出题)** —— 一道题 = 一次建模任务:agent 拿到 prompt 和 workspace 里的初始输入(图片/blend/glb/gltf/obj/fbx),用 Blender 建模并导出 model.glb;判题脚本在 agent 结束后评分。出题三步:

1. `projects.json` 列表加一条:`{"id": "project_2", "prompt": "……"}`(其余字段全部有默认,见 [docs/projects_ref.md](docs/projects_ref.md))
2. 建目录 `projects/project_2/`:`workspace/` 放 agent 可见的初始输入,`reference/` 放判题专用答案(agent 不可见),`project_2.py` 写判题
3. `python run.py check` 校验配置;`python run.py run --project project_2` 单题试跑

**Agent 开发者(接入新的 CLI 或场景后端)** —— 在 `core/wrapper/` 加实现,满足 `core/interface/` 的协议(`AgentRunner` / `SceneManager` / `Judge`),总控编排不用动;`core/controller.py: build_wrappers()` 里接线。

## 判题脚本接口

每题一个脚本(`projects/project_N/project_N.py`,与题号同名),总控用题目指定的 Python 环境执行:

```bash
python project_N.py --workspace <题目副本workspace> --run <题目输出目录> --out <score.json>
```

退出码 0 且写出 `score.json`:`{"score": 0-100, "passed": bool, "details": {...}}`。
可 import `core.utils`(无头 Blender 六视图渲染、剪影 IoU)。完整约定与可用环境变量:[docs/projects_ref.md](docs/projects_ref.md#判题脚本接口);示例:`projects/project_1/project_1.py`。

## 环境

- 开发机(Linux):`python3 envinstall.py` 安装 uv、Python 3.12.10/3.14.8 和 `.venv/py312`、`.venv/py314`;依赖按版本分文件 `requirements-3.12.txt` / `requirements-3.14.txt`。
- Docker 镜像内同样用 uv 建好两套环境(与 envinstall.py 一致)。
- Docker 构建/运行/镜像源/从 release 包装载的完整说明:[docs/docker.md](docs/docker.md)。

## 审计与安全

- 每轮对话完整留痕:`codex exec --json` 原始事件流 + stderr 逐行落盘;`round_NNN.md` 汇总该轮的提示词/思考/工具与 MCP 调用/文件改动/耗时/token;agent 的截图(MCP 返回的图像)存为 `round_NNN_images/` 下的图片文件;MCP 调用、命令执行、文件改动同时提取进 `audit.jsonl`;scene.blend 保留每题最终场景。
- **答案隔离(容器内)**:controller 镜像里 `projects/`(判题脚本 + 参考答案)仅 root 可读;总控以 root 运行,通过 `setpriv` 把 codex 降权到 `agent` 用户(`config.json` 的 `codex.user`),agent 进程读不到答案;题目 workspace 复制后 chown 给 agent。宿主机直跑(非 root / Windows)时此隔离不生效,只剩审计兜底——正式跑分请在容器内。
- 沙盒:Codex 另以 `--sandbox workspace-write` 运行(写限制在 workspace);两个容器经 compose 隔离;判题在 agent 结束后才执行。
- MCP 端点无鉴权,compose 默认只绑 `127.0.0.1`;要对局域网放开请显式改 `docker-compose.yml`。
- codex 二进制下载支持 sha256 校验(`install.sh` 第二参数);uv 经官方脚本安装。信任模型:构建期信任 GitHub Releases / astral.sh / PyPI / download.blender.org。

## 测试

```bash
python3 -m pytest tests -q     # 或 .venv/py312/bin/python -m pytest tests -q
```

`tests/` 只含总控的绿测(假 codex / 假 judge,串行执行)。

## 目录结构

```
headless-3d-bench/
├── run.py                  # 入口:run / check / envinfo / build
├── core/                   # 总控包
│   ├── controller.py       # 编排与 CLI
│   ├── settings.py         # config.json + .env
│   ├── projects.py         # 题目发现与校验
│   ├── audit.py            # 审计日志
│   ├── interface/          # 协议:AgentRunner / SceneManager / Judge
│   ├── wrapper/            # 实现:codex.py / blender_mcp.py / python_judge.py
│   └── utils/              # 判题辅助(Blender 渲染对比,供判题脚本 import)
├── config.json             # 总配置 —— 参数参考 docs/config_ref.md
├── projects.json           # 题目列表 —— 参数参考 docs/projects_ref.md
├── projects/project_N/     # 题目目录(workspace/ + reference/ + project_N.py)
├── docs/                   # 参数参考文档(config_ref / projects_ref)
├── deps/<组件>/<版本>/     # 版本化依赖(blender / blendermcp / codexcli)
├── outputs/                # 运行输出(git 忽略)
├── envinstall.py           # 环境安装
├── requirements-3.12.txt / requirements-3.14.txt
├── Dockerfile / docker-compose.yml
├── tests/devteam/          # 总控绿测(构建方)
├── tests/auditteam/        # 红队对抗测试(审计方)
├── task.md                 # 决策与工作日志
├── AGENTS.md               # agent 协作边界
└── .github/workflows/      # CI(手动触发,只构建)
```

## 待办

- codex 对第三方模型缺元数据(`Model metadata not found` 警告):把 `model_context_window` / `model_auto_compact_token_limit` 做成 `.env` 可配并写进生成的 codex config.toml,让 auto-compact 时机正确。
- GHCR 镜像发布(CI 已有手动构建,发布待指令)。
- 判题并行化(当前串行,结构上已预留)。

## License

Apache License 2.0,见 [LICENSE](LICENSE)。
