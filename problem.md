# problem.md — headless-3d-bench 红队审计报告

审计时间:2026-10-07。环境:Windows 11,Python 3.14.7,无 Docker(与 task.md 记录的环境限制一致)。

- 基线:现有绿测 26/26 通过,全部为真实测试(无空断言、无恒真断言),fake codex/fake judge 走的是真实编排代码路径。
- 红测:新增 `redteam/`(conftest + 3 个测试文件,共 32 条),**全部通过 = 32 条对抗性行为全部被证实**。复现:`python -m pytest redteam -q`(需要 pytest/numpy/Pillow)。
- 纪律说明:按 AGENTS.md,`tests/` 保持纯绿测,红测独立放 `redteam/`;本报告不改动任何产品代码。

每条问题标注:位置 / 证据(对应红测编号 R## 或静态分析)/ 影响 / 建议修复方向。严重度:P0 危及 benchmark 有效性或主流程不可用;P1 功能性缺陷;P2 健壮性/安全加固;P3 文档与一致性。

---

## P0-1 判题答案与判题代码对 agent 可读(benchmark 可被直接作弊)

**位置**:`Dockerfile`(`COPY . .`)+ `.dockerignore`(未排除 `projects/`)+ `core/controller.py:153`(codex 以 `--sandbox workspace-write` 运行)+ README「审计与安全」。

**证据**:R31 动态证实——从 agent 的工作目录取相对路径即可读到 `projects/project_1/project_1.py` 全文(含评分逻辑、PASS_THRESHOLD),同法可 `cp reference/mug.glb workspace/model.glb` 直接拿满分。容器内同样成立:projects/ 被 `COPY . .` 烘进 controller 镜像,agent(codex)就跑在 controller 容器里;codex 的 `workspace-write` 沙盒**只限制写、不限制读**。README 宣称「答案/判题不进入 agent 的视野(约定 + 审计兜底)」——「约定」不成立,只剩审计兜底(事后可查 command_execution,但分数已被污染)。叠加效果:agent 读到判题脚本后还能针对剪影 IoU 的弱点刷分(见 P2-9)。

**影响**:benchmark 分数有效性根本受损,且是 Silent 的(不查 audit 无法发现)。

**建议方向**(可组合):
1. controller 镜像不把 `projects/` 烘进去——judge 改由独立挂载/独立构建阶段注入,或运行时从外部 mount 只读卷,agent 容器与 judge 材料分离;
2. 或将判题步骤移出 agent 所在容器(如判题在另一个 `docker compose run` 中执行);
3. 最低限度:README 安全章节如实描述现状(可读、靠审计兜底),不要宣称「不进入视野」。

---

## P0-2 按 .env.example 配置后,API key 永远到不了 codex 进程

**位置**:`core/wrapper/codex.py:119-126`(`_env()`)、`.env.example`、`core/controller.py:232-234`(cmd_check 的 key 检查)。

**证据**:R22 证实——`.env` 按官方示例写 `OPENAI_API_KEY=sk-...` 时,`settings.env` 有 key,但 `_env()` 只转发 `LLM_API_KEY`,且 `.env` 从不导出到 `os.environ`,codex 子进程环境里 `OPENAI_API_KEY` 为 None。`run.py check` 的检查逻辑(`LLM_API_KEY or env_key 对应的值`)能看到 .env 里的 key,**不报 WARNING**,于是 check 全绿、run 认证失败。.env.example 全文没有出现 `LLM_API_KEY`——代码的主路径恰恰是没文档化的那个。

**影响**:文档化配置路径在主流程上不可用;用户只有在真实 run 时才发现。

**建议方向**:`_env()` 在 `LLM_API_KEY` 为空时回落到 `settings.env.get(env_key)`;或 `.env.example` 改为以 `LLM_API_KEY` 为主、provider key 仅作 env_key 指向名。两者取一并把另一处文档改一致。

---

## P1-1 判题失败不影响进程退出码,CI 会把「零评分运行」当成功

**位置**:`core/controller.py:222`(`ok = all(r["status"] == "completed")`)。

**证据**:R13 证实——judge 退出码非 0 → `judge.status == "judge_error"`,但 task 的 `status` 仍是 `"completed"`(agent 正常结束),`run.py run` 返回 0,summary 显示 `Mean score: - over 0 judged project(s)`。对比:R10/R11 中 agent 侧异常会让 rc=1。判题失败( infra 问题,如 venv 缺失、MCP 挂掉)恰恰是整套 benchmark 最常见的失败模式,却静默成功。

**建议方向**:退出码把 `judge_error` 也算失败,例如 `ok = all(r["status"] == "completed" and r.get("judge", {}).get("status") == "judged")`;或在 summary 里对未判分项目显式告警并不返 0。

## P1-2 事件流解析一崩,codex 子进程变孤儿

**位置**:`core/wrapper/codex.py:157-194`(`run_round` 的逐行处理循环)。

**证据**:R10(`usage` 字段是非数字字符串 → `int()` 抛 ValueError)、R11(图片块 base64 非法 → `b64decode` 抛异常)均证实异常直接穿透 `run_round`;R12 证实此时**从不调用 `proc.wait()`/kill,codex 子进程被遗留在后台继续跑**(测试中 30 秒的假进程在 wrapper 立即抛错后仍存活)。异常被 `run_task` 兜底记为 `status=error`,该题还会判题,但孤儿进程可能仍持有 MCP 连接/写 workspace,污染后续题目。

**建议方向**:`run_round` 的事件处理循环包 try/finally,异常路径先 `proc.kill()` + `proc.wait()`;`parse_codex_event` 内部对 `int()`/`b64decode` 做防御(坏的 usage 记 0 并留 audit 事件,坏的图片跳过并记事件),单条脏事件不应终止整轮。

## P1-3 秘密脱敏大小写敏感、关键字覆盖不全

**位置**:`core/controller.py:163-164`(`masked_env`,正则 `KEY|TOKEN|SECRET`,无 IGNORECASE)。

**证据**:R01 证实——`.env` 里写小写 `openai_api_key=sk-...` 会**原样写进 run.json**;`LLM_API_PASSWORD`、`MY_AUTH` 这类不含 KEY/TOKEN/SECRET 的 key 同样泄露。另外值级泄露未处理:`LLM_BASE_URL=https://user:pass@proxy/...` 这类内嵌凭据原样入快照。

**影响**:run.json 是要长期留档/可能上传的审计快照,泄露面随用户自定义 key 名扩大。

**建议方向**:正则加 `re.IGNORECASE` 并扩关键字(如 `KEY|TOKEN|SECRET|PASSWD|PASSWORD|AUTH|CREDENTIAL`);或反向白名单(只放行已知非敏感 key)。

## P1-4 .env 值可注入任意 TOML 进 codex 配置

**位置**:`core/wrapper/codex.py:97-112`(`config_toml`,f-string 直插、无转义)。

**证据**:R02 证实——`LLM_MODEL` 含引号+换行时,生成的 config.toml 仍被 `tomllib` 正常解析,且成功注入新键(`experimental_features = true` 生效)。同法可注入 `[model_providers.*]`、`[mcp_servers.*]` 等节,把 MCP 端点或 provider base_url 指到攻击者服务器。.env 虽属本地配置,但它会被粘贴自各种 provider 文档/群分享,注入面真实存在;至少坏值应报配置错误而不是静默生成畸形/被劫持的 TOML。

**建议方向**:写入前校验值不含 `"`/换行,或改用 `tomllib` 的反向序列化(手写转义);check 子命令对生成结果做一次 `tomllib.loads` 自检。

---

## P2 健壮性与安全加固(均已由红测证实行为)

| # | 位置 | 证实行为 | 建议 |
| --- | --- | --- | --- |
| P2-1 | `core/projects.py:44`(R03)、`core/settings.py:110`(R04) | projects.json / config.json 顶层不是对象时抛 AttributeError/TypeError 裸 traceback,而不是 ConfigError(退出码 2) | 加载后先 `isinstance(data, dict)` 校验 |
| P2-2 | `core/projects.py:31`(R05) | `min_rounds: "two"` 抛 TypeError 裸 traceback | resolved_limits 校验类型与取值范围,统一 ConfigError |
| P2-3 | `core/projects.py:24-33`(R06) | `max_turns: -3` / `0` 不校验,题目 0 轮直接结束且照常判题 | 三个 limits 均校验「正整数或 null」 |
| P2-4 | `core/projects.py:59`(R07) | `judge: "../evil.py"` 路径穿越被接受,可指到项目目录外任意文件 | judge 解析后校验 `resolve()` 仍在项目目录内 |
| P2-5 | `core/projects.py:79`(R08) | `enabled: "false"`(字符串)静默保持启用 | `enabled` 非 bool 时报 ConfigError |
| P2-6 | `core/projects.py:65`(R09)、`core/wrapper/python_judge.py:45`(R19) | `judge_timeout: "soon"` 抛 ValueError;`-5` 被 subprocess 当成立即超时 → judge_error,均不报配置错误 | load_project 时校验为正数 |
| P2-7 | `core/wrapper/python_judge.py:58`(R15) | `score: true` 通过校验(bool 是 int 子类),分数落成 `True` | 校验时排除 `bool` |
| P2-8 | `core/settings.py:88-98`(R20) | `mcp_path` 用字符串 `startswith` 判前缀,`/app/outputs_evil/...` 这类同前缀兄弟目录被误翻译 | 用 `Path.is_relative_to` 或补路径分隔符再比较 |
| P2-9 | `core/utils/compare.py:19-21,10-13`(R28/R29) | 双边空剪影 → IoU=1.0(满分);RGB 无 alpha 的渲染 → 全图当前景,IoU 虚高。叠加 P0-1(agent 可读判题源码)后,判题度量可被针对(如包围盒立方体刷六视图剪影) | compare 增加防御:参考侧空 mask 直接报错;渲染后断言存在 alpha 通道且有前景像素;判题度量弱点在 README/出题文档中如实说明 |
| P2-10 | `core/wrapper/codex.py:81-82`(R27) | 无 message 的 error 事件把**整个事件**(可能含 base64 截图,单条 10 万+字符)写进 audit.jsonl 和 result.error | `str(ev)` 前截断,或只取已知字段 |
| P2-11 | `core/wrapper/python_judge.py:32`(R32) | judge 子进程继承 controller 全部环境,含无用的 LLM API key | 只注入所需变量(PYTHONPATH/MCP_URL/H3D_*),白名单化 |
| P2-12 | `core/controller.py:118,142`(R17) | `prepare_task_dir` 在 try 之外:workspace 复制失败 → 整轮崩溃,该题无 result.json,后续题目全部不执行;同理 judge.run 在 try 之外,判题期 KeyboardInterrupt/异常 → 该题 result 丢失、audit 未关闭 | 把 prepare/judge 也纳入每题 try,单题失败不拖垮整轮 |
| P2-13 | `core/wrapper/codex.py:114-117`(静态) | 宿主机跑 `run.py run` 会无备份覆盖真实 `~/.codex/config.toml` | 写前备份已有文件,或写入前提示;文档注明 |
| P2-14 | `docker-compose.yml:39` + `deps/blender/5.2.2/mcp_http.py`(静态) | MCP 端点无鉴权且端口发布到宿主机所有网卡(`8000:8000` = 0.0.0.0),局域网任意机器可执行任意 Blender Python。README 有文字警告,但默认配置本身是暴露的 | 默认绑 `127.0.0.1:8000:8000`,需要远程时显式放开 |
| P2-15 | `core/wrapper/codex.py:54-60`(R21)+ task.md 风险清单 | usage 只认 `input_tokens/output_tokens` 两组键,其他键名静默计 0 → `max_tokens` 可能永远不触发;真实 codex 0.160 的事件 schema 未验证(task.md 已列) | 首次 Docker 冒烟时核对真实 usage 事件;解析侧对未知 usage 键记 audit 警告 |
| P2-16 | `projects.json`(project_1 `judge_timeout: 900`)vs `core/utils/blender_render.py:83`(单次渲染 read timeout 600s)×2 次渲染(静态) | 判题渲染预算上限(1200s+)超过判题总超时(900s),慢渲染时 judge 会被总控杀掉 → judge_error | judge_timeout 提到 >2×渲染预算,或渲染超时改总预算制 |
| P2-17 | `core/wrapper/blender_mcp.py:24-34`(静态) | `read_timeout_seconds` 是单次读超时,不是总超时;MCP 服务器保持连接慢慢吐数据时 reset/save 可无限挂起 → 整轮 benchmark 挂死 | 外层加总超时(asyncio.wait_for) |
| P2-18 | `deps/codexcli/0.160.0/install.sh`、`envinstall.py:46`(静态) | codex 二进制与 uv 安装均无校验和/签名验证(`curl \| sh` / 直接 tar 解压),供应链风险 | 固定 sha256 校验;至少在文档中声明信任模型 |

---

## 测试覆盖审计

### 绿测真实性结论

26 条绿测全部为真:有实质断言、走真实编排(`controller.main` → `run_task` → 真实 `PythonJudge` 子进程),fake 的只是进程外部的 codex CLI。未发现假绿/恒真/空测。`conftest.py` 的 fake codex 事件流覆盖了 usage/mcp_call/command/file_change/reasoning/image/agent_message 七类事件,与解析器分支一一对应,质量良好。

### 已有代码但完全/部分未测(且本机条件足够,建议补测)

按价值排序:

1. **`core/wrapper/python_judge.py` 全部失败路径**(判题非零退出/非法 score.json/超时/缺文件)——R13/R14/R15/R16/R18/R19 已用假 judge 证明全部可在本机测,现零覆盖。这是判题链路最常见的失败面,**必须补**。
2. **`CodexWrapper._env()` key 转发**——P0-2 的回归测试,一行断言的事,**必须补**。
3. **scene_error 路径**(reset 失败 → 跳过判题、result 落盘)——R23 证明 monkeypatch `execute_blender_code` 即可,无需 Blender,**建议补**(这是「宁可失败不可污染」的核心设计,目前无测试守护)。
4. **`masked_env`**——P1-3 的回归测试,纯函数,**建议补**。
5. **`core/utils/compare.py`**——纯 numpy/PIL,R28/R29/R30 已证明可测,现零覆盖。**建议补**(判题正确性的地基)。
6. **`projects/project_1/project_1.py` 的缺 model.glb 路径**——R24 证明不需要 Blender 即可测(直接子进程跑真实判题脚本,断言 score 0/exit 0)。task.md 决策 17「project 脚本暂不做测试」对本路径不成立(它是「条件不足漏测」中被误判的一类),**建议补**。
7. **配置解析的对抗输入**(R03/R04/R05/R08/R09)——纯函数,随修复一起补回归测试。
8. `create_run_dir` 冲突后缀、`safe_name`、`write_summary` 无评分分支、`git_rev` 异常分支——可测,价值低,**可选**。

### 条件不足、本机无法测(不补,建议列入首次 Docker 冒烟清单)

- `core/wrapper/blender_mcp.py` 真实 MCP 往返、`core/utils/blender_render.py` 真实渲染、`deps/blender/5.2.2/headless_server.py` 与 `mcp_http.py`、`entrypoint.sh`、两个 Dockerfile、compose healthcheck。
- `codex exec --json` 真实事件 schema(usage 键名、resume --last 行为)——task.md 风险清单已列,建议冒烟时顺带核对 P2-15。
- mcp-for-blender 2.1.3 addon 与 Blender 5.2.2 的私有 API 兼容(`_drain_command_queue` 等)——task.md 已列。

### 不需要测的

`run.py`(12 行转发)、`core/audit.py`(纯 append,已被绿测间接覆盖)、`envinstall.py`(OS  installer,宿主相关,价值低)。

---

## 文档问题清单(与代码/数据不符处)

1. **题目 prompt 与实际输入不符(P3,影响 agent 发挥)**:`projects.json` 的 prompt 称 refs/ 下有「front/back/left/right/top 正交视图 + iso」共 6 张;实际 `projects/project_1/workspace/refs/` 只有 4 张:`front.png / iso.png / side.png / top.png`(无 back/left/right,多了 prompt 没提的 side)。agent 按 prompt 找不到 back/left/right 会困惑或臆造。改 prompt 或补齐图。
2. **README 安全宣称不实(P0-1 的文档面)**:「答案/判题不进入 agent 的视野」在当前架构下不成立,需按 P0-1 修复或改写。
3. **task.md 备忘行陈旧/串行粘贴错误**:「22 绿测通过」与 T16 的 26 条矛盾(实测 26 通过);该备忘后半段(「VPS docker 实测通过后才打包发布:22 绿测通过;compose/workflow…」)是 T13 验证文字的重复粘贴,语义不通,需清理。
4. **README 目录结构缺项**:未列 `docs/`、`task.md`、`AGENTS.md`、`.env.example`(T15 之后 docs/ 已存在)。
5. **docs/projects_ref.md 状态表不全**:缺 `interrupted`(KeyboardInterrupt 路径会写入 result.json)与初始态 `pending` 的说明。
6. **`.env.example` 缺 `LLM_API_KEY` 字段**:与 P0-2 一体两面——代码主路径无文档,文档路径无代码支持。
7. **docs/config_ref.md deps 节**:「缺失会在 build 时报错」实际是 KeyError 裸 traceback(R25),建议改为 ConfigError 或文档如实描述。
8. **`run.py run` 退出码语义未文档化**:判题失败仍返 0(P1-1)、`max_turns` 等正常限流结束返 1,使用者无法从文档预知;修复 P1-1 后请在 docs/projects_ref.md 补退出码表。

---

## 附:红测清单(32 条,全部证实)

`redteam/test_red_config.py`:R01 脱敏泄露、R02 TOML 注入、R03/R04 顶层非对象崩溃、R05 limits 类型崩溃、R06 负 max_turns、R07 judge 路径穿越、R08 enabled 字符串、R09 judge_timeout 类型。
`redteam/test_red_controller.py`:R10/R11 脏事件崩轮、R12 子进程孤儿、R13 judge_error 退出码 0、R14 非法 score.json、R15 bool 分数、R16 缺 score.json、R17 复制失败拖垮整轮、R18/R19 判题超时、R20 mcp_path 前缀误配、R21 usage 静默计 0。
`redteam/test_red_judge.py`:R22 key 不到 codex、R23 scene_error 路径、R24 真实判题缺模型路径、R25 build 缺 deps KeyError、R26 disabled 题目报 unknown、R27 error 事件全量入审计、R28 RGB 全前景、R29 双空满分、R30 尺寸不匹配。
`redteam/test_red_leakage.py`:R31 agent 读判题源码(答案泄露)、R32 judge 继承 API key。
