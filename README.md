# headless-3d-bench

无头 3D 建模 agent benchmark:**Codex CLI 通过 BlenderMCP 指挥无头 Blender 建模,Python 总控驱动全部流程,每题由独立判题脚本评分,全程可审计。**

目标平台:纯 Linux Docker(Windows 开发请用 WSL2 + Docker)。

## 工作流程

```
project.json + .env          配置(版本、模型、API、默认 limits、追问话术)
      │
projects/project_N/          题目:task.json(prompt/limits/python/judge)+ workspace/(初始输入)+ reference/(判题答案,agent 不可见)
      │
      ▼  python run.py run
outputs/<开始时间>_<模型>_v<benchmark版本>/project_N/
      ├── workspace/         题目副本,Codex 的工作目录(输入 + 产物 model.glb)
      ├── rounds/            每轮 codex --json 原始事件流 + stderr
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

# 2. 构建镜像(版本号从 project.json 读)
python3 run.py build            # 需要 python3 可用;或直接 docker compose build

# 3. 跑 benchmark(controller 容器 + blender 容器自动编排)
docker compose run --rm controller python run.py run

# 只跑某题 / 校验配置 / 跑测试
docker compose run --rm controller python run.py run --project project_1
docker compose run --rm controller python run.py check
docker compose run --rm --no-deps controller        # pytest(不拉起 blender)
```

## 配置

**`.env`**(API 与 URL,见 `.env.example`):`LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_ENV_KEY` / `LLM_WIRE_API` / `LLM_MODEL` / `LLM_API_KEY`,`MCP_URL`。总控据此生成 `~/.codex/config.toml`。

**`project.json`**(总配置):
- `benchmark.version`:benchmark 总版本号(输出目录名的一部分)
- `deps`:blender / blendermcp / codexcli 版本,对应 `deps/` 下的版本目录
- `python.envs`:判题环境(默认 `.venv/py312` = 3.12.10、`.venv/py314` = 3.14.8)
- `codex`:codex 二进制与 sandbox 模式
- `scene`:每题前重置 / 结束后保存 scene.blend
- `defaults.nag_prompt`:追问话术(全局)
- `defaults.limits`:三项限制的默认值

**题目 `projects/project_N/task.json`**:

```json
{
  "id": "project_1",
  "prompt": "……",
  "python": "3.12",
  "limits": {"min_rounds": 1, "max_turns": 10, "max_tokens": null}
}
```

三个限制是 **AND 关系,任一到达即结束,各自可设 null(不限)**:
- `min_rounds`:最少轮次——agent 说完成了也按 `nag_prompt` 纯文字追问(不泄露分数),直到满 N 轮;1 = 说完成就结束;null = 一直追问直到其他限制
- `max_turns`:最大轮次
- `max_tokens`:累计 token 上限(从 codex `--json` 事件流统计)
- agent 达到最大上下文 → 直接结束(`context_limit`)

## 判题脚本接口

每题一个脚本(`projects/project_N/project_N.py`,与题号同名),总控用题目指定的 Python 环境执行:

```bash
python project_N.py --workspace <题目副本workspace> --run <题目输出目录> --out <score.json>
```

退出码 0 且写出 `score.json`:`{"score": 0-100, "passed": bool, "details": {...}}`。
可 import `core.utils`(经 BlenderMCP 的无头 Blender 六视图渲染 `blender_render`、剪影 IoU `compare`);判题环境可用 `MCP_URL` 等环境变量。示例见 `projects/project_1/project_1.py`。

## 环境

- 开发机(Linux):`python3 envinstall.py` 安装 uv、Python 3.12.10/3.14.8 和 `.venv/py312`、`.venv/py314`;依赖按版本分文件 `requirements-3.12.txt` / `requirements-3.14.txt`。
- Docker 镜像内同样用 uv 建好两套环境(与 envinstall.py 一致)。

## 审计与安全

- 每轮 `codex exec --json` 的原始事件流逐行落盘;MCP 工具调用单独提取进 `audit.jsonl`;scene.blend 保留每题最终场景。
- 沙盒:Codex 以 `--sandbox workspace-write` 运行(写限制在 workspace);两个容器经 compose 隔离;`projects/`(含判题答案)不挂载进任何容器,只烘焙在 controller 镜像内;判题在 agent 结束后才执行。
- MCP 端点无鉴权,仅在受信网络/本机使用。

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
│   ├── settings.py         # project.json + .env
│   ├── projects.py         # 题目发现与校验
│   ├── audit.py            # 审计日志
│   ├── interface/          # 协议:AgentRunner / SceneManager / Judge
│   ├── wrapper/            # 实现:codex.py / blender_mcp.py / python_judge.py
│   └── utils/              # 判题辅助(Blender 渲染对比,供判题脚本 import)
├── projects/project_N/     # 题目(task.json + workspace/ + reference/ + project_N.py)
├── deps/<组件>/<版本>/     # 版本化依赖(blender / blendermcp / codexcli)
├── outputs/                # 运行输出(git 忽略)
├── envinstall.py           # 环境安装
├── requirements-3.12.txt / requirements-3.14.txt
├── Dockerfile / docker-compose.yml
├── tests/                  # 总控绿测
└── .github/workflows/      # CI(手动触发,只构建)
```
