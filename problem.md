# problem.md — headless-3d-bench 红队审计报告(T17 后复测)

复测时间:2026-10-07。环境:Windows 11,Python 3.14.7,无 Docker。

## 复测总结论

上一轮 32 条红测发现的问题,T17 修复有效:**21 条原漏洞断言全部由红转失败(=漏洞已修)**,逐条复核代码确认修复正确(答案隔离、key 转发回落、退出码、解析加固、配置校验、脱敏、TOML 校验、judge 环境白名单等),修复质量良好,无敷衍式修复。

auditteam 已按 AGENTS.md 纪律复测更新:已修问题翻转为安全回归断言(断言「期望的安全行为」),仍开放的断言保持红。当前状态:**devteam 44 绿;auditteam 33/35 绿,2 红 = 2 个开放问题**。

以下为仍存在的问题(已全部清除已修复项)。

---

## 开放问题(红测仍红 / 文档仍不符)

### 1. 题目 prompt 与实际参考图不符(上轮文档问题 1,未修;影响上生产跑分)

**位置**:`projects.json` project_1 的 `prompt`。

**证据**:prompt 称「参考图在当前 workspace 的 refs/ 目录下(front/back/left/right/top 正交视图 + iso)」,实际 `projects/project_1/workspace/refs/` 只有 4 张:`front.png / iso.png / side.png / top.png`——没有 back/left/right,多了 prompt 没提的 side。注意 **task.md T17 条目声称「文档问题 1~8 全部修正(prompt 与实际 refs 一致)」,实际未改**,该日志记录不实。

**影响**:被测 agent 按 prompt 找不到 back/left/right 视图,会困惑、臆造或浪费轮次,直接影响跑分公平性。上生产前必须修。

**建议方向**:改 prompt 使其与实际 4 张图一致(推荐),或补齐 back/left/right 渲染图。

### 2. provider 名注入 TOML 节标题未校验(R02b,红)

**位置**:`core/wrapper/codex.py:131`(`[model_providers.{provider}]` 节标题直接 f-string 插值)。

**证据**:红测 `test_r02b_provider_name_must_be_validated`。T17 给所有**值**加了 `_toml_str` 校验,但节标题里的 provider 名漏了:
- `LLM_PROVIDER=open.router` → 生成合法但语义错误的嵌套表 `[model_providers.open.router]`,codex 按名查找 provider 会静默失配,运行期才爆认证错误;
- `LLM_PROVIDER=bad]` → TOML 非法,tomllib 自检抛 `TOMLDecodeError` 而非 `ConfigError`,穿透 `main()` 的 ConfigError 处理变成裸 traceback(退出码 1 而非 2)。

**建议方向**:provider 名加白名单校验(如 `^[A-Za-z0-9_-]+$`,codex provider 名实际就是这个字符集),在 `config_toml()` 或 settings 加载时拒绝;同时把 tomllib 自检的 TOMLDecodeError 包装成 ConfigError。

### 3. disabled 题目也被全量校验,半成品题目会拖垮整个运行(R33,红;T17 新引入)

**位置**:`core/projects.py:95-100`(`discover_projects` 对**所有**条目调用 `load_project`,再过滤 enabled)。

**证据**:红测 `test_r33_disabled_project_should_not_need_valid_files`:加一条 `enabled: false` 且目录不存在的题目(WIP 草稿的正常用法),`run`/`check` 直接 ConfigError 中止。T17 之前 disabled 条目在校验前就被跳过。与 `docs/projects_ref.md`「`false` 时跳过该题,不用删条目」矛盾——现在是「跳过运行但不跳过校验」。

**建议方向**(二选一,需决策):a) disabled 条目跳过 `load_project`(仅校验 id 格式与重复),恢复文档语义;b) 保留全量校验(防配置腐坏也有道理),但改文档明确「disabled 题目也必须结构完整」。红队倾向 a。

---

## 观察项(不阻塞上生产,无需立即修复)

- **宿主机直跑无答案隔离**(R31 仍过 = 符合预期):root+容器外运行时 codex 不降权,agent 可读判题源码。README 已如实写明「正式跑分请在容器内」,可接受。容器侧隔离(useradd agent + `chmod -R o-rwx /app/projects` + setpriv)只能静态核验(R31b),**首次 Docker 冒烟时必须实测**:以 agent 身份 `cat /app/projects/project_1/project_1.py` 应 Permission denied。
- **codex 0.160 真实 usage 事件 schema** 仍未验证(task.md 风险清单已有):若真实键名不在 KNOWN_USAGE_KEYS 内,`max_tokens` 会静默失效;T17 已加 `usage_unknown_keys` audit 事件,冒烟时检查 audit.jsonl 是否出现该事件即可确认。
- **判题度量对 agent 半透明**:README/docs/tests 在镜像内 agent 可读,六视图剪影 IoU 的判题思路是公开的(阈值与参考答案已 root-only 保护)。若 benchmark 设计要求度量保密,需把 docs/README 中判题细节也移出镜像;否则现状可接受,建议在 README 明确「度量公开、答案保密」的设计立场。
- **masked_env 不遮盖无密码的 URL userinfo**(`http://token@host`):`_URL_USERINFO_RE` 要求 `user:pass@` 才替换。边缘情况,知道即可。
- **`.env.example` 仍未提 `LLM_API_KEY`**:代码侧已修(回落到 provider env_key),文档写法现在能正常工作;但 task.md T17 声称「.env.example 补 LLM_API_KEY」实际未改(日志不实,同问题 1)。建议在 .env.example 补一行注释说明优先级:`LLM_API_KEY` 优先,缺省回落到 `LLM_ENV_KEY` 指定的变量。

---

## 红测现状(35 条)

`tests/auditteam/` 当前 33 绿 2 红,红的两条即上述问题 2、3。修复后应全绿。
R01-R32 为首轮发现(21 条已修并翻转为回归断言,11 条为正确行为/已缓解项的钉死),R31b/R33/R02b 为本轮新增。
复现:`python -m pytest tests/auditteam -q`(需 pytest/numpy/Pillow;无网络/Docker/Blender 依赖)。
