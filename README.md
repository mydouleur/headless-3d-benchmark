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

**`.env`**(API 与 URL,见 `.env.example` 的完整示例):`LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_ENV_KEY` / `LLM_WIRE_API` / `LLM_MODEL` / `LLM_API_KEY`,`MCP_URL`,`H3D_MCP_OUTPUTS_PREFIX`(可选,路径翻译)。总控据此生成 `~/.codex/config.toml`。

**`config.json`**(总配置,仓库内有完整示例,每个字段都在用):

| 字段 | 含义 | 默认 |
| --- | --- | --- |
| `benchmark.name` / `benchmark.version` | benchmark 名称 / 总版本号(输出目录名的一部分) | 必填 |
| `deps.blender` / `deps.blendermcp` / `deps.codexcli` | 组件版本,对应 `deps/<组件>/<版本>/` 目录 | 必填 |
| `python.default` | 判题默认环境(`python.envs` 的键) | `3.12` |
| `python.envs` | 环境键 → venv 路径(`.venv/py312` = 3.12.10,`.venv/py314` = 3.14.8) | 两套 |
| `codex.binary` | codex 可执行文件 | `codex` |
| `codex.sandbox` | codex sandbox 模式 | `workspace-write` |
| `codex.extra_args` | 插在 `codex` 与 `exec` 之间的全局参数(如 `-c key=value`) | `[]` |
| `scene.reset` / `scene.save_blend` | 每题前重置场景 / 结束后保存 scene.blend | `true` / `true` |
| `defaults.nag_prompt` | 追问话术(全局,纯文字,不含分数) | 内置中文话术 |
| `defaults.limits` | 三项限制的全局默认(见下) | `min_rounds:1, max_turns:10, max_tokens:null` |
| `projects_dir` / `outputs_dir` | 题目目录 / 输出目录 | `projects` / `outputs` |

**题目列表 `projects.json`**(根目录,列表形式;benchmark 制作者在这里增删题目,`enabled: false` 可临时禁用;每题按 `id` 对应 `projects/<id>/` 目录):

```json
{
  "projects": [
    {
      "id": "project_1",
      "enabled": true,
      "prompt": "……",
      "python": "3.12",
      "limits": {"min_rounds": 1, "max_turns": 10, "max_tokens": null}
    }
  ]
}
```

每题字段:**加粗为必填**;不写就回落到 `config.json` 的默认:

| 字段 | 含义 | 缺省行为 |
| --- | --- | --- |
| **`id`** | `project_<数字>`,对应 `projects/<id>/` 目录 | 必填,不可重复 |
| **`prompt`** | 首轮对话的完整任务描述 | 必填 |
| `enabled` | 是否参与运行 | `true` |
| `python` | 判题环境键 | `config.json` 的 `python.default` |
| `judge` | 判题脚本文件名 | `<id>.py`(与题号同名) |
| `judge_timeout` | 判题超时(秒) | 600 |
| `limits` | 三项限制(见下) | 整体/逐字段回落到 `defaults.limits` |

三个限制是 **AND 关系,任一到达即结束,各自可设 null(不限)**:
- `min_rounds`:最少轮次——agent 说完成了也按 `nag_prompt` 纯文字追问(不泄露分数),直到满 N 轮;1 = 说完成就结束;null = 一直追问直到其他限制
- `max_turns`:最大轮次
- `max_tokens`:累计 token 上限(从 codex `--json` 事件流统计)
- agent 达到最大上下文 → 直接结束(`context_limit`)

## 给两类使用者

**Benchmark 制作者(出题)** —— 一道题 = 一次建模任务:agent 拿到 prompt 和 workspace 里的初始输入(图片/blend/glb/gltf/obj/fbx),用 Blender 建模并导出 model.glb;判题脚本在 agent 结束后评分。出题三步:

1. `projects.json` 列表加一条:`{"id": "project_2", "prompt": "……"}`(其余字段全部有默认,见上表)
2. 建目录 `projects/project_2/`:`workspace/` 放 agent 可见的初始输入,`reference/` 放判题专用答案(agent 不可见),`project_2.py` 写判题(接口见下)
3. `python run.py check` 校验配置;`python run.py run --project project_2` 单题试跑

**Agent 开发者(接入新的 CLI 或场景后端)** —— 在 `core/wrapper/` 加实现,满足 `core/interface/` 的协议(`AgentRunner` / `SceneManager` / `Judge`),总控编排不用动;`core/controller.py: build_wrappers()` 里接线。

## 判题脚本接口

每题一个脚本(`projects/project_N/project_N.py`,与题号同名),总控用题目指定的 Python 环境执行:

```bash
python project_N.py --workspace <题目副本workspace> --run <题目输出目录> --out <score.json>
```

退出码 0 且写出 `score.json`:`{"score": 0-100, "passed": bool, "details": {...}}`。
可 import `core.utils`(经 BlenderMCP 的无头 Blender 六视图渲染 `blender_render`、剪影 IoU `compare`)。示例见 `projects/project_1/project_1.py`。

判题脚本可用的环境变量(总控注入):`MCP_URL`(Blender MCP 端点)、`H3D_OUTPUTS_DIR`(输出根目录)、`H3D_MCP_OUTPUTS_PREFIX`(MCP 服务器侧路径前缀)、`PYTHONPATH`(仓库根,保证能 import `core.utils`)。

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
│   ├── settings.py         # config.json + .env
│   ├── projects.py         # 题目发现与校验
│   ├── audit.py            # 审计日志
│   ├── interface/          # 协议:AgentRunner / SceneManager / Judge
│   ├── wrapper/            # 实现:codex.py / blender_mcp.py / python_judge.py
│   └── utils/              # 判题辅助(Blender 渲染对比,供判题脚本 import)
├── config.json             # 总配置(版本/deps/默认 limits/nag_prompt)
├── projects.json            # 题目列表(增删题目只改这里)
├── projects/project_N/      # 题目目录(workspace/ + reference/ + project_N.py)
├── deps/<组件>/<版本>/     # 版本化依赖(blender / blendermcp / codexcli)
├── outputs/                # 运行输出(git 忽略)
├── envinstall.py           # 环境安装
├── requirements-3.12.txt / requirements-3.14.txt
├── Dockerfile / docker-compose.yml
├── tests/                  # 总控绿测
└── .github/workflows/      # CI(手动触发,只构建)
```
