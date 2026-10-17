# Nefex Web UI

基于 [Open WebUI](https://github.com/open-webui/open-webui) 的自托管 AI 工作台。本分支移除了 Ollama 原生接入，通过 OpenAI 兼容接口连接本地或云端模型，并提供完整的 Docker 部署。

![Open WebUI](./banner.png)

## Docker 安装

需要 Docker Engine / Docker Desktop 和 Compose v2。

```bash
curl -fL https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/latest/download/install.sh -o /tmp/nefex-install.sh
bash /tmp/nefex-install.sh --install-dir ./nefex-web-ui
```

在终端直接运行会进入中文交互向导，依次配置 CPU/CUDA、镜像方式、端口与访问地址、模型接口、SearXNG/DeepSeek 和管理员账户。密钥隐藏输入，确认前显示配置摘要；取消不会修改原配置。默认拉取成品镜像，自动部署应用、PostgreSQL / pgvector、MinIO、Redis、SearXNG，生成随机密钥和私有存储桶，并等待健康检查及真实服务检查通过。

安装完成后打开向导中设置的地址（默认 <http://localhost:3000>）。可在向导中预设管理员，或选择首次访问网页创建；模型接口和 DeepSeek 密钥也可稍后在管理后台填写。

```bash
# 只配置，不启动服务
./scripts/install-docker.sh --interactive --init-only
# 自动化安装，不显示交互提示
./scripts/install-docker.sh --non-interactive
```

## 更新已有安装

v0.12.7 起，安装器支持更新、完整备份和回滚。已有配置的终端用户直接运行安装器可选择维护操作：

```bash
./scripts/install-docker.sh --update
# 指定版本；自动化执行另加 --non-interactive
./scripts/install-docker.sh --update --to v0.12.7
./scripts/install-docker.sh --backup
./scripts/install-docker.sh --list-backups
./scripts/install-docker.sh --rollback /完整备份目录
```

v0.12.4 / v0.12.5 没有更新命令时，下载新版入口，指定原安装目录即可：

```bash
curl -fL https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/latest/download/install.sh -o /tmp/nefex-install.sh
bash /tmp/nefex-install.sh --update --install-dir /opt/nefex-web-ui
```

更新前校验发布包、拉取目标镜像，随后暂停服务并保存配置及全部数据卷。保留原项目名、密钥、账号、文件和后台设置；失败自动恢复。回滚恢复到备份时的数据，操作前另存当前状态。支持现有 Nefex PostgreSQL/MinIO Docker 布局；SQLite 或本地文件旧布局需单独迁移。详细行为与备份说明见[安装文档](docs/docker-install.md#管理更新与备份)。

- [完整安装、配置、备份与迁移](docs/docker-install.md)
- [验证记录与测试范围](docs/docker-validation.md)
- [镜像构建与发布](https://github.com/Albert-Li-Sz/Nefex-Web-UI/actions/workflows/docker.yaml)

## 镜像

| 组件                  | 镜像与构建方式                                                                                                                         |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Nefex Web UI          | `ghcr.io/albert-li-sz/nefex-web-ui:latest`，amd64 / arm64；复用官方 Open WebUI v0.12.3 完整运行环境，仅重建修改的前后端并补充 Chromium |
| CUDA                  | `ghcr.io/albert-li-sz/nefex-web-ui:cuda`，amd64；复用官方 CUDA 运行环境                                                                |
| PostgreSQL / pgvector | `pgvector/pgvector:0.8.7-pg17-bookworm`，直接使用扩展上游镜像                                                                          |
| Redis                 | 官方 `redis:7.4-alpine`，锁定摘要                                                                                                      |
| SearXNG               | 官方 `searxng/searxng`，锁定摘要                                                                                                       |
| MinIO                 | `ghcr.io/albert-li-sz/nefex-web-ui-minio:RELEASE.2025-10-15T17-29-55Z`，amd64 / arm64；所需修复版按上游要求源码构建                    |

需要自己构建时执行 `./scripts/install-docker.sh --build`。镜像中的模型与 Python 依赖继承自固定版本的官方镜像，不重复编译。完全自定义依赖的构建入口保留在 `deploy/Dockerfile.source`。

## 功能

保留聊天与流式输出、视觉模型、工具调用、知识库与 RAG、联网搜索、网页读取、文档解析、离线 Pyodide 代码执行、本地嵌入、本地 Whisper、语音、图像生成与编辑、笔记、记忆、频道、用户与权限、OAuth / LDAP、工具与函数、外部 MCP / OpenAPI 集成。联网搜索默认使用 SearXNG，也支持在管理后台接入 [DeepSeek Search / Fetch](docs/docker-install.md#deepseek-search--fetch)。

模型推理、图像服务、SSO、云盘等功能需要配置对应服务与凭据。安装器提供应用和基础设施，不提供第三方模型账户。已有 SQLite、文件和向量数据的迁移见安装说明。

## 项目来源与许可

界面名称为 **Nefex Web UI (Open WebUI)**，保留 Open WebUI 名称、图标、署名与许可证。上游代码遵循 [LICENSE](LICENSE)、[LICENSE_HISTORY](LICENSE_HISTORY) 和 [LICENSE_NOTICE](LICENSE_NOTICE)；Nefex 新增原创代码的许可见 [LICENSE_NEFEX](LICENSE_NEFEX)。MinIO 与其他依赖保留各自许可。

Open WebUI 由 [Timothy Jaeryang Baek](https://github.com/tjbck) 和上游贡献者创建。[上游文档](https://docs.openwebui.com/)适用于本分支保留的功能；部署方式与已移除的接入以本仓库文档为准。
