# Docker 部署与镜像说明

目标平台:**纯 Linux**。Windows 开发请用 WSL2 + Docker Desktop。

## 两个镜像

| 镜像 | 内容 | 大小 |
| --- | --- | --- |
| `headless-3d-bench`(controller) | python 3.12.10 + uv 建的 `.venv/py312`(3.12.10)与 `.venv/py314`(3.14.8)+ Codex CLI 静态二进制 + bubblewrap + 项目代码;总控以 root 运行,codex 降权到 `agent` 用户 | ~1.4 GB |
| `headless-3d-bench-blender` | Debian trixie + Blender 官方 tarball(LTS)+ mcp-for-blender(pin 版本)+ headless_server / mcp_http;暴露 Streamable HTTP MCP | ~2.3 GB |

版本全部来自根目录 `config.json` 的 `deps`(见 [config_ref.md](config_ref.md#deps))。

## 构建

```bash
python3 run.py build        # = docker compose build + 从 config.json 注入版本号
```

网络受限环境(如国内 VPS)可用的构建参数(compose 从同名环境变量读):

| 参数 | 用途 | 示例 |
| --- | --- | --- |
| `PIP_INDEX_URL` | PyPI 镜像 | `https://mirrors.cloud.tencent.com/pypi/simple` |
| `APT_MIRROR` | Debian 镜像 | `mirrors.cloud.tencent.com` |
| `CODEX_MIRROR` | GitHub release 代理前缀 | `https://gh-proxy.com/https://github.com` |

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
APT_MIRROR=mirrors.cloud.tencent.com \
CODEX_MIRROR=https://gh-proxy.com/https://github.com \
python3 run.py build
```

## 运行

```bash
docker compose up -d blender                                    # 无头 Blender + MCP(默认只绑 127.0.0.1:8000)
docker compose run --rm controller python run.py run            # 全量(自动等 blender healthy)
docker compose run --rm controller python run.py run --project project_1
docker compose run --rm --no-deps controller                    # 绿测(不拉起 blender)
```

- `outputs/` 同时挂载进两个容器(同路径 `/app/outputs`),agent 产物、scene.blend、判题渲染都在里面。
- `projects/`(判题答案)**只烘进 controller 镜像且仅 root 可读**;agent(codex)以 `agent` 用户运行,读不到。
- MCP 端点**无鉴权**,默认绑 localhost;要对外开放请显式改 `docker-compose.yml` 的端口绑定。

## 从 release 的镜像包安装(不构建)

```bash
# 下载 release 附件后:
gunzip -c headless-3d-bench_v*_images.tar.gz | docker load
docker compose up -d blender
```

## 已知运行时注意事项(实测记录)

- **codex 0.160+ 移除了 `wire_api="chat"`**:provider 必须支持 `/responses`(OpenAI 官方、DeepSeek 官方、OpenRouter 均可);DeepSeek 的 `base_url` 用 `https://api.deepseek.com/v1`。
- **headless 下 MCP 审批**:controller 用 `--dangerously-bypass-approvals-and-sandbox` 跑 codex(`--approve-for-me` 的自动评审依赖 OpenAI 专有模型,第三方 provider 不可用;容器内 bwrap 无 namespace 权限)。真实隔离 = 容器 + agent 用户,见 README「审计与安全」。
- 第三方模型不在 codex 内置元数据表里,会有 `Model metadata ... not found` 警告(见 README 待办)。
- 2GB 内存的 VPS 可跑(实测),构建期建议有 swap。
