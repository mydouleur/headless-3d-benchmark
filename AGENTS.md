# AGENTS.md

给在本仓库工作的 agent 的约定。

## 这是什么

无头 3D 建模 benchmark:Codex CLI 经 BlenderMCP 指挥无头 Blender;`core/` 是 Python 总控;`projects/project_N/` 是题目;判题由每题的 `project_N.py` 完成。目标平台纯 Linux Docker。

## 铁律

1. **改结构先改 task.md**:task.md 记录全部已确认决策、版本、目录结构和工作日志;每完成一个 task 本地提交一次并在 task.md 备注 commit。
2. **测试只写绿测**:`tests/` 只测总控的正向路径,串行执行(不加 xdist);红测/对抗测试由其他监督 agent 负责,不要插手。
3. **版本不猜**:deps 版本(Blender LTS、mcp-for-blender、Codex CLI、Python)必须先 web 核实再写;project.json 是唯一版本来源。
4. **判题接口不可破坏**:`project_N.py --workspace --run --out`,score.json = `{score:0-100, passed:bool, details:{}}`,退出码 0。改接口必须同步改 `core/wrapper/python_judge.py`、README 和所有 projects。
5. **追问不泄露分数**:nag_prompt 只给文字,绝不把判题分数发给 agent。
6. **秘密不落库**:.env 被 git/docker 忽略;日志与快照里的 key 一律脱敏(masked_env)。

## 常用命令

```bash
python run.py check                 # 校验配置与题目
python -m pytest tests -q           # 总控绿测(需要 pytest;环境见 envinstall.py)
python run.py build                 # docker compose build(版本来自 project.json)
docker compose run --rm controller python run.py run
```

## 结构约定

- 新 agent CLI / 场景后端:在 `core/wrapper/` 加实现,实现 `core/interface/` 里的协议,不动 `core/controller.py` 的编排。
- 新题目:`projects/project_<下一个编号>/`,含 task.json、workspace/(agent 可见的初始输入)、reference/(判题专用,agent 不可见)、project_N.py(与题号同名的判题脚本)。
- 判题公共能力放 `core/utils/`;判题私有多环境依赖走对应 `requirements-3.1x.txt`。
- 新增 Blender 版本:复制 `deps/blender/<旧版本>/` 为新版本目录再改,不要原地改旧版本。
