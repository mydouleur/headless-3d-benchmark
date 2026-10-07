# config.json 参数参考

benchmark 总配置,位于仓库根目录。仓库自带的 `config.json` 就是完整可用示例,本文件逐字段说明。

## 完整示例

```json
{
  "benchmark": {
    "name": "headless-3d-bench",
    "version": "0.1.0"
  },
  "deps": {
    "blender": "5.2.2",
    "blendermcp": "2.1.3",
    "codexcli": "0.160.0"
  },
  "python": {
    "default": "3.12",
    "envs": {
      "3.12": ".venv/py312",
      "3.14": ".venv/py314"
    }
  },
  "codex": {
    "binary": "codex",
    "sandbox": "workspace-write",
    "extra_args": []
  },
  "scene": {
    "reset": true,
    "save_blend": true
  },
  "defaults": {
    "nag_prompt": "还没有达到要求。请检查你当前的模型……",
    "limits": {
      "min_rounds": 1,
      "max_turns": 10,
      "max_tokens": null
    }
  },
  "projects_dir": "projects",
  "outputs_dir": "outputs"
}
```

## benchmark

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `name` | 是 | 报错退出(配置错误,退出码 2) | benchmark 名称,写入 run.json 和 summary |
| `version` | 是 | 报错退出 | **benchmark 总版本号**,输出目录名的一部分:`outputs/<时间>_<模型>_v<版本>/` |

## deps

组件版本号,按 `deps/<组件>/<版本>/` 目录解析;`run.py build` 时注入 docker 构建参数。三个都必填,缺失会在 build 时报错。升版本的方式:复制 `deps/<组件>/` 下的旧版本目录为新版本号再修改,然后改这里。

| 字段 | 当前值 | 对应位置 |
| --- | --- | --- |
| `blender` | `5.2.2`(最新 LTS) | `deps/blender/5.2.2/`(容器 Dockerfile 等) |
| `blendermcp` | `2.1.3`(最新) | `deps/blendermcp/2.1.3/requirements.txt`(pip pin) |
| `codexcli` | `0.160.0`(最新 stable) | `deps/codexcli/0.160.0/install.sh` |

## python

判题脚本的多版本环境(防依赖地狱)。

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `default` | 否 | `"3.12"` | 题目未指定 `python` 时使用的环境键 |
| `envs` | 否 | `{"3.12": ".venv/py312", "3.14": ".venv/py314"}` | 环境键 → venv 目录。键是题目里 `python` 字段引用的名字;目录由 `envinstall.py` 创建(3.12.10 / 3.14.8) |

题目写了 `python: "3.14"` 但环境没装时,该题判题报 `judge_error`(不影响其他题)。

## codex

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `binary` | 否 | `"codex"` | codex 可执行文件(镜像内已装;测试可指到假 CLI) |
| `sandbox` | 否 | `"workspace-write"` | codex 沙盒模式,写操作限制在题目 workspace |
| `extra_args` | 否 | `[]` | 插在 `codex` 与 `exec` 之间的**全局参数**(如 `["-c", "model_reasoning_effort=high"]`);不是 exec 子命令参数 |

## scene

每题的 Blender 场景管理(经 BlenderMCP 的 `execute_blender_code`)。

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `reset` | 否 | `true` | 每题开始前清空场景(`read_factory_settings(use_empty=True)` + orphans purge)。**重置失败该题直接判 `scene_error`**,宁可失败不可污染 |
| `save_blend` | 否 | `true` | 每题结束后把场景存为题目输出目录的 `scene.blend`(留痕;失败仅警告不判负) |

## defaults

全局默认值,可被 `projects.json` 里每题同名字段覆盖(见 [projects_ref.md](projects_ref.md))。

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `nag_prompt` | 否 | 内置中文话术(`core/settings.py: DEFAULT_NAG_PROMPT`) | agent 说完成但未满 `min_rounds` 时的追问内容。**纯文字,绝不能含分数**(防止过拟合) |
| `base_prompt` | 否 | 内置 benchmark 规则话术(`core/settings.py: DEFAULT_BASE_PROMPT`) | 全局首轮提示词:与每题 `prompt` 合并成第一轮 user message(`base_prompt + "---" + task prompt`)。放 benchmark 规则(model.glb 输出契约、坐标约定、无头纪律)。**不替代 codex 自带的 system prompt**——codex 的 harness 是评测对象的一部分,刻意保留 |
| `limits` | 否 | `{"min_rounds": 1, "max_turns": 10, "max_tokens": null}` | 三项限制的全局默认,语义见 [projects_ref.md](projects_ref.md#limits) |

## projects_dir / outputs_dir

| 字段 | 必填 | 不写时 | 说明 |
| --- | --- | --- | --- |
| `projects_dir` | 否 | `"projects"` | 题目目录根,`projects.json` 里的 id 索引到 `<projects_dir>/<id>/` |
| `outputs_dir` | 否 | `"outputs"` | 运行输出根目录(git 忽略) |

## 相关文件

- API/URL 配置在 `.env`,见根目录 `.env.example`(含全部字段注释)。
- 题目列表与每题参数:[docs/projects_ref.md](projects_ref.md)。
