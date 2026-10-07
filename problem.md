# problem.md — headless-3d-bench 红队审计报告(T18 后复测)

复测时间:2026-10-07。环境:Windows 11,Python 3.14.7,无 Docker。

## 复测总结论

- 首轮 32 条红测发现:T17 全部修复有效(21 条漏洞断言转红=已修,逐条复核代码确认)。
- 上轮遗留 3 项:**R02b(provider 名注入)、R33(disabled 题目全量校验)T18 已修**,复核代码无误;`.env.example` 也补了 provider 命名校验说明。
- 当前测试:devteam 46 绿;auditteam 36 条,**35 绿 1 红**。

## 仍开放的问题(只剩 1 个,阻塞上生产跑分)

### 题目 prompt 与实际参考图不符(首轮文档问题 1,至今未修)

**位置**:`projects.json` project_1 的 `prompt`。

**证据**:红测 `test_r34_prompt_matches_actual_refs`(新增,对真实仓库做一致性校验)——
prompt 声称 refs/ 下有「front/back/left/right/top 正交视图 + iso」,实际只有 `front.png / iso.png / side.png / top.png`:**back/left/right 不存在,side 未在 prompt 中提及**。
另:task.md T17 条目声称「文档问题 1~8 全部修正(prompt 与实际 refs 一致)」,实际 projects.json 从未被修改,该日志记录不实,需一并更正。

**影响**:被测 agent 按 prompt 找不到 back/left/right 视图,会困惑、臆造或浪费轮次,直接影响跑分公平性。这是 benchmark 题目自身的 bug,上生产前必须修。

**建议方向**:改 prompt 使其与实际 4 张图一致(推荐,一行的事),或补齐 back/left/right 渲染图;同时把 task.md T17 的记录改如实。

---

## 观察项(不阻塞,无需立即修复)

- **宿主机直跑无答案隔离**(R31 仍过=符合预期):容器外 codex 不降权。README 已如实写明「正式跑分请在容器内」。容器侧隔离(useradd agent + `chmod -R o-rwx /app/projects` + setpriv)只做了静态核验(R31b),**首次 Docker 冒烟必须实测**:以 agent 身份 `cat /app/projects/project_1/project_1.py` 应 Permission denied。
- **codex 0.160 真实 usage 事件 schema 未验证**(task.md 风险清单已有):冒烟时检查 audit.jsonl 是否出现 `usage_unknown_keys` 事件即可确认 max_tokens 统计是否有效。
- **判题度量对 agent 半透明**:README/docs/tests 在镜像内 agent 可读,剪影 IoU 思路公开(阈值与参考答案已 root-only 保护)。若设计要求度量保密需再收紧;否则建议在 README 明确「度量公开、答案保密」的立场。
- **masked_env 不遮盖无密码的 URL userinfo**(`http://token@host`):正则要求 `user:pass@` 才替换。边缘情况。

---

## 红测现状(36 条)

`tests/auditteam/`:35 绿 1 红,红的即上述开放问题(R34)。
R01-R32 为首轮发现(已修项已翻转为安全回归断言);R02b/R31b/R33/R34 为后续轮新增。
复现:`python -m pytest tests/auditteam -q`(需 pytest/numpy/Pillow;无网络/Docker/Blender 依赖)。
修完 R34 对应的问题后,若 auditteam 全绿且无新问题,本文件可按约定删除。
