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
5. **`.env.example` 配 API 和 URL**;**`config.json`** 配:benchmark 总版本号、deps 各组件版本、默认 python、codex 参数、limits 默认值。题目的单独版本号不做——版本号是 benchmark 总版本号。
6. **deps/ 按组件/版本号存放**:`deps/blender/5.2.2/`、`deps/blendermcp/2.1.3/`、`deps/codexcli/0.160.0/`;`config.json` 选版本;版本取最新 LTS,无 LTS 取 latest(已 web 核实,见下)。
7. **题目在 `projects/project_N/`**,按题号走;每个题目录下有 `workspace/`(题目材料:blend/glb/gltf/obj/fbx/图片等初始输入)和判题脚本(与题目一一对应)。
8. **运行时总控把题目复制到 `outputs/<开始时间>_<模型id>_<benchmark版本号>/project_N/`**,agent 在副本的 workspace 里工作;一题一脚本一输出。
9. **每题串行**:先(reset)blender+mcp、再启动 codex;题目结束后总控立即执行该题判题脚本,并记录答题时间。整体串行(节约内存),设计上为以后并行留口。
10. **续轮机制**:codex exec 单次执行 = 一轮;续轮用 `codex exec resume --last`;每题可配"agent 说完成就结束"或"总控纯文字追问继续(min_rounds)";追问**不泄露分数**。
11. **三个上限 AND 关系,任一到达即结束,各自可设为不限**:min_rounds(最少轮次)、max_turns(最大轮次)、max_tokens(累计 token);agent 达到最大上下文 → 默认直接结束。
12. **审计**:每轮 codex --json 原始事件流、stdout/stderr、MCP 工具调用提取、场景 scene.blend、配置快照、判题输出全部落盘。
13. **沙盒**:controller 容器 + blender 容器 compose 隔离;agent 侧用 codex 自带 sandbox(workspace-write),工作区挂载精确控制;答案/判题不进入 agent 视野(约定 + 审计兜底)。
14. **判题接口**:`python project_N.py --workspace <dir> --run <dir> --out <score.json>`(判题脚本与题号同名),score.json = `{score:0-100, passed:bool, details:{}}`,退出码 0=判题正常执行。示例 project_1 带无头 Blender 渲染对比(经 MCP 让 Blender 渲染六视图,与参考渲染比 silhouette IoU)。
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
│   ├── settings.py         # config.json + .env 加载
│   ├── projects.py         # 题目发现与校验
│   ├── audit.py            # 审计日志
│   ├── interface/          # 协议:AgentRunner / SceneManager / Judge
│   └── wrapper/            # 实现:codex.py / blender_mcp.py / python_judge.py
├── envinstall.py           # 环境安装:uv → python 3.12.10/3.14.8 → .venv/py312|py314
├── config.json            # benchmark 版本号、deps 版本选择、默认 python、limits 默认值
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
│       └── project_N.py    # 判题脚本(与题号同名,接口见上)
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
- [x] T2 config.json + .env.example + .gitignore/.gitattributes/.dockerignore
- [x] T3 envinstall.py + requirements-3.12/3.14.txt
- [x] T4 配置加载/codex config/运行目录与题目复制 —— 83200bd
- [x] T5 codex 包装(轮次/resume/token/上下文上限/审计) —— 83200bd
- [x] T6 MCP 场景管理 + 判题调度(多 venv) —— 83200bd
- [x] T4b 重构:controller.py 拆为 core/(settings/projects/audit/controller)+ core/interface(协议)+ core/wrapper(codex/blender_mcp/python_judge),根目录留 run.py 入口 —— f1a3e37
- [x] T7 projects/project_1 示例 + judge_helpers(blender_render/compare) —— 8d1af2e
- [x] T7b 审阅修正:判题脚本改名 project_N.py(与题号同名,task.json 可不写 judge 字段);judge_helpers 移入 core/utils/ —— 0c7367f
- [x] T8 deps:blender 5.2.2 容器(移植 headless_server/mcp_http,适配 Blender 5.x)+ codexcli 0.160.0 install.sh + blendermcp 2.1.3 pin —— eff0055
- [x] T9 根 Dockerfile + docker-compose.yml —— eff0055
- [x] T10 tests/ 总控绿测(串行) —— dfa3727
- [x] T11 CI workflow(手动触发,只构建) —— 9b6a825;修复 name 内冒号 YAML 错误
- [x] T12 README.md + AGENTS.md —— b6a0316
- [x] T8b 结构调整:project.json → config.json;每题 task.json 合并为根目录 projects.json(列表、enabled 开关、按 id 索引 projects/<id>/);24 绿测通过 —— 0be9a7f
- [x] T13 总核验:24 绿测通过;compose/workflow YAML、config.json、projects.json 校验通过;shell 脚本语法通过;host 上 check 仅因 .venv 未装而报(预期,需 Linux/WSL2+Docker)
- [x] T14 审阅修正:projects_dir 参数失效修复(projects.py 改用 settings.projects_dir);.env.example 补 H3D_MCP_OUTPUTS_PREFIX;README 补全 config.json/projects.json 全字段表(judge/judge_timeout/enabled 等此前未文档化的参数)、判题环境变量、「给两类使用者」章节;确认 limits 缺省回落 defaults 正常 —— 598991f
- [x] T15 参数文档独立成 docs/config_ref.md + docs/projects_ref.md(完整示例 + 每字段"不写时如何生效");README 只留相对链接指引 —— a7595a1
- [x] T16 留痕增强:codex wrapper 逐轮提取思考/工具与 MCP 调用/命令执行/文件改动并写 round_NNN.md;MCP 返回的截图落盘 round_NNN_images/;新增 config.json defaults.base_prompt(全局首轮提示词,与题目 prompt 合并;codex 自带 system prompt 刻意保留作为评测对象);26 绿测通过 —— 2004b82
- [x] T17 红队审计修复(problem.md 全部条目):
  - P0-1 答案泄露:容器内 projects/ 仅 root 可读,codex 经 setpriv 降权为 agent 用户(config.json codex.user),workspace chown 给 agent;宿主机非 root 时无隔离(文档如实说明)
  - P0-2 key 转发:codex _env() 回落到 provider env_key;.env.example 补 LLM_API_KEY
  - P1-1 判题失败退出码改 1;P1-2 事件循环 try/finally kill + 防御性解析;P1-3 脱敏大小写+扩关键字+URL 凭据;P1-4 TOML 值校验 + tomllib 自检
  - P2-1~18 逐项:类型/范围校验、judge 路径穿越拦截、enabled 布尔校验、bool 分数拒绝、mcp_path is_relative_to、compare 空/RGB 参考防御、error 截断、judge 环境白名单、prepare/judge 纳入 try、codex 配置写前备份、MCP 端口默认绑 127.0.0.1、未知 usage 键记 audit、judge_timeout 1500、MCP 总超时 wait_for、install.sh 可选 sha256
  - 文档问题修正:安全宣称改写、目录结构补全、状态表补 interrupted/pending、退出码表、deps 报错改 ConfigError。注:其中「prompt 与实际 refs 一致」「.env.example 补 LLM_API_KEY」两项因脚本中断当时未实际生效,T19 才真正落地
  - 测试重组:tests/devteam(绿测 44 条,含 18 条修复回归)+ tests/auditteam(红队 32 条,不动);AGENTS.md 明确边界
  - 红测复跑:21 条由红转失败 = 漏洞已修;11 条仍过(其中 R23/R24/R30 是正确行为断言,R09/R21/R28/R29 为已缓解并文档化,R31 仅容器内生效)
  —— e856ab7(测试重组 9568ae5)
- [x] T18 红队二轮:修 R02b(LLM_PROVIDER 名称校验)+ R33(disabled 题目不再要求文件存在);devteam 补 2 条回归钉 —— f6de5ee + 0df190a(文档)
- [x] T19 红队三轮:修 R34(project_1 prompt 与实际 4 张 refs 一致:front/side/top + iso)+ 补回 T17 丢失的 .env.example LLM_API_KEY 主路径注释;更正 T17 日志不实之处 —— 913b952
- [x] T20 红队终审通过,problem.md 按约定删除;补 task.md 遗漏的 T17/T18 hash;加 Apache-2.0 LICENSE;推送 GitHub —— 本次提交
- [x] T21 VPS 端到端实测通过(2026-10-07,腾讯云 2GB):
  - 构建:双镜像成功(blender 2.33GB / controller 1.44GB);期间修复 blender 官方包缺 libxkbcommon/libGL 等 X 库、内置 python 路径 glob、codex 安装 CRLF+重试+CODEX_MIRROR、PIP_INDEX_URL/APT_MIRROR 构建参数
  - 冒烟:Blender 5.2.2 LTS 经 MCP 执行代码 OK;agent 用户读 /app/projects 被拒(Permission denied);codex mcp list 可见 blender
  - codex 0.160 实测:wire_api=chat 已移除(DeepSeek 官方支持 /v1/responses,base_url 用 /v1);MCP 审批需 --approve-for-me,但其 auto-review 依赖 OpenAI 专有模型,第三方 provider 不可用 → 容器内用 --dangerously-bypass-approvals-and-sandbox(真实隔离=容器+agent uid);bwrap 在容器内无 namespace 权限,同上 bypass
  - judge 修复:reference 模型不在共享挂载内,渲染前 stage 到 outputs
  - 真实端到端(deepseek-chat):project_1 完成,1 轮 20 分钟 490 万 token,**99.59 分 passed**(六视图 IoU 0.99),留痕完整(rounds/scene.blend/renders/audit)
  - 测试环境泄漏修复:devteam fixture 隔离 LLM_* 环境变量、codex.user 置空;容器内 46 绿测全过
  —— 多个本地提交,待网络恢复后统一 push
- [x] T22 docs/docker.md 补全(镜像内容/构建参数/运行/已知运行时注意事项);README+AGENTS 加待办区(模型上下文窗口配置等);README 目录结构补 docs/docker.md 链接 —— 本次提交
- 备忘:绿测为纯模拟(假 codex/假 judge,不碰网络/Blender/Docker);VPS docker 实测通过后才打包发布

## 风险/待验证

- mcp-for-blender 2.1.3 的 addon 与 Blender 5.2.2 的兼容性(旧项目基于 4.3 + 2.1.0)——无 docker 环境,暂无法验证,容器首跑时确认。
- codex exec --json 的 token 用量事件格式、resume --last 在容器内的行为——需真实环境冒烟。
- 本机(Win11 无 WSL2/docker)无法构建镜像;Docker 相关只做静态核验,实跑待用户环境。
