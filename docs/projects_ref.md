# projects.json 参数参考

题目列表,位于仓库根目录。benchmark 制作者增删改题目只动这个文件 + `projects/<id>/` 目录。

## 完整示例

```json
{
  "projects": [
    {
      "id": "project_1",
      "enabled": true,
      "prompt": "做一个马克杯的三维模型……参考图在当前 workspace 的 refs/ 目录下……",
      "python": "3.12",
      "judge": "project_1.py",
      "judge_timeout": 900,
      "limits": {
        "min_rounds": 1,
        "max_turns": 10,
        "max_tokens": null
      }
    },
    {
      "id": "project_2",
      "enabled": false,
      "prompt": "做一张餐桌……"
    }
  ]
}
```

`project_2` 展示了最小写法:除 `id`/`prompt` 外全部省略,走默认值。

## 每题字段

| 字段 | 必填 | 不写时如何生效 | 说明 |
| --- | --- | --- | --- |
| `id` | **是** | — | `project_<数字>`,对应 `projects/<id>/` 目录;不可重复(重复报配置错误) |
| `prompt` | **是** | — | 首轮对话的完整任务描述,agent 的唯一目标输入 |
| `enabled` | 否 | `true`(参与运行) | `false` 时跳过该题,不用删条目 |
| `python` | 否 | 用 `config.json` 的 `python.default`(默认 `3.12`) | 判题脚本的 Python 环境键,见 [config_ref.md](config_ref.md#python) |
| `judge` | 否 | `<id>.py`(与题号同名) | 判题脚本文件名,位于 `projects/<id>/` 内 |
| `judge_timeout` | 否 | 600 秒 | 判题超时;超时记 `judge_error`(渲染类判题建议 900+) |
| `limits` | 否 | 整体回落到 `config.json` 的 `defaults.limits`;写了部分字段时,缺的字段也逐个回落 | 见下节 |

## limits

三个限制是 **AND 关系:任一到达立即结束该题对话;各自可设 `null` 表示不限**。但三个全 null(含回落后)是配置错误——必须至少有一个兜底。

| 字段 | 缺省(`defaults.limits`) | 语义 |
| --- | --- | --- |
| `min_rounds` | `1` | 最少轮次。agent 说完成时若不足 N 轮,总控按 `nag_prompt` **纯文字追问**(不含分数)继续;`1` = 说完成就结束;`null` = 一直追问直到其他限制触发 |
| `max_turns` | `10` | 最大轮次(每次 `codex exec` 调用 = 1 轮;续轮用 `codex exec resume --last` 保持同一对话) |
| `max_tokens` | `null`(不限) | 累计 token 上限(input+output,从 codex `--json` 事件流的 usage 统计) |

另有一条固定规则,不在 limits 里:**agent 达到模型最大上下文 → 直接结束**,状态记 `context_limit`。

### 题目状态(写入 `result.json`)

| status | 含义 |
| --- | --- |
| `completed` | agent 完成且满足 min_rounds(正常结束) |
| `max_turns` / `max_tokens` | 触上限结束 |
| `context_limit` | 达到模型上下文上限 |
| `codex_error` | codex 进程非零退出 |
| `scene_error` | 场景重置失败(该题未运行) |
| `error` | 其他异常 |

注意:除 `scene_error` 外,判题脚本**总会执行**(哪怕 agent 失败)——判题要对缺失/不完整产物鲁棒,自行给低分。

## 题目目录结构

```
projects/<id>/
├── workspace/        # 初始输入:图片 / blend / glb / gltf / obj / fbx 等,agent 可见
├── reference/        # 判题专用答案(如参考模型),agent 不可见,判题脚本可读
└── <id>.py           # 判题脚本
```

运行时总控把 `workspace/` 复制到 `outputs/<时间>_<模型>_v<版本>/<id>/workspace`,agent 只在副本里工作,原题目录不被污染。

## 判题脚本接口

```bash
python <id>.py --workspace <题目副本workspace> --run <题目输出目录> --out <score.json>
```

- 退出码 0 且写出 `score.json`:`{"score": 0-100, "passed": bool, "details": {...}}`
- 总控注入的环境变量:`MCP_URL`、`H3D_OUTPUTS_DIR`、`H3D_MCP_OUTPUTS_PREFIX`、`PYTHONPATH`(仓库根,可 `from core.utils import blender_render, compare`)
- 完整示例:`projects/project_1/project_1.py`(无头 Blender 六视图渲染 + 剪影 IoU 对比参考答案)

## 相关文件

- 全局默认与组件版本:[docs/config_ref.md](config_ref.md)
- API/URL:根目录 `.env.example`
