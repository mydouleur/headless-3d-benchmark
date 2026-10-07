# task.md — headless-3d-bench 工作日志

> 纪律：每完成一个 task 本地提交一次，并在本文件对应条目后备注 commit hash 与结果。
> 本文件记录全部已确认的需求与决策，防止遗忘。改动需求时先改这里。

## 目标(一句话)

纯 Linux Docker 下运行的 3D 建模 benchmark:**无头 Blender + BlenderMCP + Codex CLI(经 MCP 指挥 Blender)**,Python 总控单文件(`controller.py`)驱动，题目/判题/环境/版本全部配置化，全程可审计。

## 已确认决策(与用户对齐)

1. **全新仓库** `headless-3d-bench/`,与旧项目 `Image-similarity-benchmark/` 断联(旧目录保留不动,仅作参考)。git 先只本地提交;GitHub remote 由用户后续解决网络后建立(public)。
2. **目标平台纯 Linux Docker**;Windows 开发要求 WSL2 + Docker(本机暂无,由用户解决)。
3. **总控 = 根目录单个 Python 文件 `controller.py`**(先单文件,后续过大再拆)。
4. **环境 = 根目录 `.venv/`**,git 不上传;内含两套独立 venv:`.venv/py312`(Python 3.12.10,默认)、`.venv/py314`(Python 3.14.8)。由根目录 `envinstall.py` 安装(curl 装 uv → uv 装 Python → 建 venv → 装 requirements)。requirements 按 Python 版本分文件:`requirements-3.12.txt` / `requirements-3.14.txt`,防依赖地狱。
5. **`.env.example` 配 API 和 URL**;**`project.json`** 配:benchmark 总版本号、deps 各组件版本、默认 python、codex 参数、limits 默认值。题目的单独版本号不做——版本号是 benchmark 总版本号。
6. **deps/ 按组件/版本号存放**:`deps/blender/5.2.2/`、`deps/blendermcp/2.1.3/`、`deps/codexcli/0.160.0/`;`project.json` 选版本;版本取最新 LTS,无 LTS 取 latest(已 web 核实,见下)。
7. **题目在 `projects/project_N/`**,按题号走;每个题目录下有 `workspace/`(题目材料:blend/glb/gltf/obj/fbx/图片等初始输入)和判题脚本(与题目一一对应)。
8. **运行时总控把题目复制到 `outputs/<开始时间>_<模型id>_<benchmark版本号>/project_N/`**,agent 在副本的 workspace 里工作;一题一脚本一输出。
9. **每题串行**:先(reset)blender+mcp、再启动 codex;题目结束后总控立即执行该题判题脚本,并记录答题时间。整体串行(节约内存),设计上为以后并行留口。
10. **续轮机制**:codex exec 单次执行 = 一轮;续轮用 `codex exec resume --last`;每题可配"agent 说完成就结束"或"总控纯文字追问继续(min_rounds)";追问**不泄露分数**。
11. **三个上限 AND 关系,任一到达即结束,各自可设为不限**:min_rounds(最少轮次)、max_turns(最大轮次)、max_tokens(累计 token);agent 达到最大上下文 → 默认直接结束。
12. **审计**:每轮 codex --json 原始事件流、stdout/stderr、MCP 工具调用提取、场景 scene.blend、配置快照、判题输出全部落盘。
13. **沙盒**:controller 容器 + blender 容器 compose 隔离;agent 侧用 codex 自带 sandbox(workspace-write),工作区挂载精确控制;答案/判题不进入 agent 视野(约定 + 审计兜底)。
14. **判题接口**:`python judge.py --workspace <dir> --run <dir> --out <score.json>`,score.json = `{score:0-100, passed:bool, details:{}}`,退出码 0=判题正常执行。示例 project_1 带无头 Blender 渲染对比(经 MCP 让 Blender 渲染六视图,与参考渲染比 silhouette IoU)。
15. **模型/API 只全局配置**(.env),不做每题覆盖。
16. **CI**:`.github/workflows/docker.yml` 只配置构建,手动触发(workflow_dispatch);发布(release 挂镜像 tar.gz)等用户指令再接。
17. **测试纪律**:`tests/` 只测总控,只写绿测(正向路径);红测由其他监督 agent 负责,不插手;测试串行执行(不用 xdist);project 多环境私有脚本暂不做测试。

## 版本(2026-10 核实,均经 web 查证,非猜测)

| 组件 | 版本 | 依据 |
| --- | --- | --- |
| Blender | 5.2.2 LTS | blender.org LTS 页:5.2 LTS 更新至 5.2.2(2026-09-15),支持到 2028-07 |
| mcp-for-blender | 2.1.3 | PyPI(pepy):2026-09-30 发布;原 blender-mcp 已改名 |
| Codex CLI | 0.160.0(rust-v0.160.0) | GitHub API releases/latest(2026-10-04 查) |
| Python 默认 | 3.12.10 | 用户指定 |
| Python 备选 | 3.14.8 | python.org:2026-09-30 发布 |

## 目录结构

```
headless-3d-bench/
├── run.py                  # 入口:python run.py run|check|envinfo|build
├── core/                   # 总控包
│   ├── controller.py       # 编排:运行目录、题目复制、轮次循环、判题调度、CLI
│   ├── settings.py         # project.json + .env 加载
│   ├── projects.py         # 题目发现与校验
│   ├── audit.py            # 审计日志
│   ├── interface/          # 协议:AgentRunner / SceneManager / Judge
│   └── wrapper/            # 实现:codex.py / blender_mcp.py / python_judge.py
├── envinstall.py           # 环境安装:uv → python 3.12.10/3.14.8 → .venv/py312|py314
├── project.json            # benchmark 版本号、deps 版本选择、默认 python、limits 默认值
├── .env.example            # LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / MCP_URL ...
├── requirements-3.12.txt   # py312 判题环境依赖
├── requirements-3.14.txt   # py314 判题环境依赖
├── Dockerfile              # controller 镜像(python + uv + 两套 .venv + codex)
├── docker-compose.yml      # controller + blender 双服务,blender healthcheck
├── .dockerignore .gitignore .gitattributes
├── deps/
│   ├── blender/5.2.2/      # Dockerfile + entrypoint.sh + headless_server.py + mcp_http.py
│   ├── blendermcp/2.1.3/   # 版本 pin 说明(装 blender 镜像时 pip 此版本)
│   └── codexcli/0.160.0/   # install.sh(GitHub release musl 静态二进制)
├── projects/
│   └── project_1/
│       ├── workspace/      # 题目初始输入(图片/模型等)
│       ├── reference/      # 判题用参考答案(agent 不可见)
│       └── judge.py        # 判题脚本(接口见上)
├── outputs/                # 运行输出(git 忽略):<时间>_<模型>_<版本>/project_N/...
├── tests/                  # 总控绿测(pytest,串行)
└── .github/workflows/docker.yml
```

## 输出目录(每次运行)

```
outputs/20261004_153000_gpt-5-codex_v0.1.0/
├── run.json                # 配置快照(脱敏)、版本戳、汇总
├── summary.md
└── project_1/
    ├── workspace/          # 题目副本,agent 的工作目录(输入 + agent 产物)
    ├── rounds/round_001.jsonl(.stderr.log)…   # codex --json 原始事件流
    ├── audit.jsonl         # 总控事件:轮次/token/MCP 调用提取/限制触发
    ├── scene.blend         # 题目结束时的 Blender 场景
    ├── judge/score.json(+stdout/stderr.log)
    └── result.json         # 状态、轮数、token、耗时(答题时间)、分数
```

## Task 清单(每完成一项 = 一次本地提交)

- [x] T1 骨架 + task.md —— 4911fa5
- [x] T2 project.json + .env.example + .gitignore/.gitattributes/.dockerignore
- [x] T3 envinstall.py + requirements-3.12/3.14.txt
- [x] T4 配置加载/codex config/运行目录与题目复制 —— 83200bd
- [x] T5 codex 包装(轮次/resume/token/上下文上限/审计) —— 83200bd
- [x] T6 MCP 场景管理 + 判题调度(多 venv) —— 83200bd
- [x] T4b 重构:controller.py 拆为 core/(settings/projects/audit/controller)+ core/interface(协议)+ core/wrapper(codex/blender_mcp/python_judge),根目录留 run.py 入口 —— 本次提交
- [x] T7 projects/project_1 示例 + judge_helpers(blender_render/compare) —— 本次提交
- [x] T8 deps:blender 5.2.2 容器(移植 headless_server/mcp_http,适配 Blender 5.x)+ codexcli 0.160.0 install.sh + blendermcp 2.1.3 pin —— eff0055
- [x] T9 根 Dockerfile + docker-compose.yml —— eff0055
- [x] T10 tests/ 总控绿测(串行) —— 本次提交
- [x] T11 CI workflow(手动触发,只构建) —— 9b6a825;修复 name 内冒号 YAML 错误
- [x] T12 README.md + AGENTS.md —— b6a0316
- [x] T13 总核验:22 绿测通过;compose/workflow YAML、project.json、task.json 校验通过;shell 脚本语法通过;host 上 check 仅因 .venv 未装而报(预期,需 Linux/WSL2+Docker)

## 风险/待验证

- mcp-for-blender 2.1.3 的 addon 与 Blender 5.2.2 的兼容性(旧项目基于 4.3 + 2.1.0)——无 docker 环境,暂无法验证,容器首跑时确认。
- codex exec --json 的 token 用量事件格式、resume --last 在容器内的行为——需真实环境冒烟。
- 本机(Win11 无 WSL2/docker)无法构建镜像;Docker 相关只做静态核验,实跑待用户环境。
