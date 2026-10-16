# Nefex Web UI Docker 完整安装

Nefex Web UI 通过 OpenAI 兼容接口接入模型。Ollama 原生接口、连接管理、模型拉取、嵌入引擎和云搜索已移除。

## 一键安装

需要 Docker Engine / Docker Desktop，以及支持 `up --wait` 的 Compose v2。支持 Linux amd64 / arm64 与 macOS Docker Desktop；默认安装只拉取镜像，无需 Node.js、Python 或 Go。请预留至少 20 GB 磁盘空间，运行建议分配至少 4 GB 内存。只有使用 `--build` 源码构建时才需要至少 8 GB 内存；默认前端构建堆上限为 6 GB，可通过 `BUILD_NODE_OPTIONS=--max-old-space-size=8192` 调整。

```bash
git clone https://github.com/Albert-Li-Sz/Nefex-Web-UI.git
cd Nefex-Web-UI
./scripts/install-docker.sh
```

脚本创建权限为 `600` 的 `.env`，为数据库、Redis、MinIO、应用会话和搜索自动生成独立随机密钥，拉取 Nefex Web UI、MinIO 和其他上游镜像，启动服务，等待健康检查，然后执行真实服务检查。再次执行会保留配置、密钥和数据卷。

应用默认地址为 <http://localhost:3000>。首次打开创建管理员账户，然后在「管理员设置 → 连接」填写模型 API 地址与密钥。也可先生成配置再编辑：

```bash
./scripts/install-docker.sh --init-only
# 编辑 .env 中的 OPENAI_API_BASE_URL、OPENAI_API_KEY
./scripts/install-docker.sh
```

本地模型服务可使用 `http://host.docker.internal:端口/v1`；容器内的 `localhost` 指应用容器自身。反向代理部署时设置 `WEBUI_URL=https://你的域名`，为 WebSocket 配置升级转发，并把 `FORWARDED_ALLOW_IPS` 设置为实际代理的地址。

单独检查已有安装：

```bash
./scripts/install-docker.sh --check
```

检查会验证应用与数据库健康、pgvector 相似度查询、MinIO 上传/下载/删除、Redis 读写与发布订阅、SearXNG JSON 接口及有效上游搜索结果、Chromium 启动。搜索仅返回空结果时检查会失败，请查看搜索服务日志和出站网络。

## 服务与数据

| 服务         | 用途                                                      | 持久化卷                             |
| ------------ | --------------------------------------------------------- | ------------------------------------ |
| `open-webui` | 前后端、文件解析、RAG、语音、浏览器抓取、插件             | `open-webui`，包括缓存与本地工作文件 |
| `postgres`   | PostgreSQL 17 与 pgvector，业务数据与知识库向量           | `postgres`                           |
| `minio`      | S3 文件存储                                               | `minio`                              |
| `redis`      | 实时 WebSocket、会话、缓存、后台任务；数据库 1 供搜索使用 | `redis`，启用 AOF                    |
| `searxng`    | 聚合搜索，启用 JSON                                       | `searxng`                            |
| `minio-init` | 一次性、幂等创建私有存储桶                                | 不另存数据，成功后退出               |

只有 WebUI 默认映射到宿主机端口。其余服务通过 Compose 网络访问。修改 `COMPOSE_PROJECT_NAME` 会创建独立的一套数据卷，适合测试或多实例部署。

MinIO 使用上游修复版本 `RELEASE.2025-10-15T17-29-55Z` 源码构建，避免依赖已停止发布该修复版本的旧容器标签。[上游发布说明](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)。PostgreSQL 镜像包含 [pgvector 扩展](https://github.com/pgvector/pgvector#docker)。SearXNG 按[官方容器文档](https://docs.searxng.org/admin/installation-docker.html)配置并锁定镜像摘要，可通过 `SEARXNG_IMAGE` 显式升级。

## 功能配置

默认使用完整镜像，不使用 slim。应用基于固定摘要的官方 Open WebUI v0.12.3 运行环境，继承其 Python 依赖、嵌入模型、Whisper 和离线 Pyodide 资源；只重建本分支前后端并补充 Chromium。知识库、文档解析、本地嵌入、Whisper 语音识别、浏览器 TTS、Pyodide 代码执行、笔记、记忆、频道、工具与函数均保留；Chromium 在构建阶段安装，模型直接复用官方镜像。

| 功能                                                          | 配置                                                                                                                                                              |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 聊天、视觉理解、工具调用                                      | 填写 `OPENAI_API_BASE_URL`、`OPENAI_API_KEY`；能力取决于所选模型                                                                                                  |
| 联网搜索与网页读取                                            | 已自动连接 SearXNG；网页读取使用内置 Playwright Chromium                                                                                                          |
| 知识库 / RAG                                                  | 默认本地 `all-MiniLM-L6-v2`，384 维，向量存入 PostgreSQL；关闭自动更新以使用镜像内模型。更换本地模型需要下载时，可显式设置 `RAG_EMBEDDING_MODEL_AUTO_UPDATE=true` |
| 外部嵌入服务                                                  | 设置 `RAG_EMBEDDING_ENGINE=openai`、模型、`RAG_OPENAI_API_*` 和匹配的 `PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH`；更换模型后需重新索引知识库                         |
| 语音识别 / 朗读                                               | 本地 Whisper / 浏览器朗读可直接使用；外部引擎可填写 `AUDIO_STT_*`、`AUDIO_TTS_*`                                                                                  |
| 图片生成 / 编辑                                               | 填写 `IMAGES_OPENAI_API_*`、`IMAGES_EDIT_OPENAI_API_*`，再启用 `ENABLE_IMAGE_GENERATION`、`ENABLE_IMAGE_EDIT`；模型与其他参数可在管理员设置填写                   |
| 实时语音、OAuth、LDAP、Google Drive、OneDrive、外部工具服务器 | 在管理员设置配置相应服务与凭据；这些供应商服务不由本安装器创建                                                                                                    |
| CUDA 加速嵌入与语音                                           | 安装 NVIDIA Container Toolkit 后运行 `./scripts/install-docker.sh --gpu`；使用基于官方 CUDA 运行环境的 Nefex 镜像                                                 |

模型调用、图像服务、SSO 与云盘需要有效的服务端点或账户；安装基础设施不会自动提供模型或第三方凭据。`.env` 也会作为应用环境文件加载，其他高级功能可直接添加项目支持的环境变量。管理员保存的设置优先于首次启动的环境默认值。

## 管理、升级与备份

```bash
docker compose ps -a
docker compose logs -f open-webui
docker compose logs --tail 100 searxng
# 更新并拉取发布镜像
./scripts/install-docker.sh
# 停止服务，保留全部数据
docker compose down
```

默认应用镜像为 `ghcr.io/albert-li-sz/nefex-web-ui:latest`，MinIO 为 `ghcr.io/albert-li-sz/nefex-web-ui-minio:RELEASE.2025-10-15T17-29-55Z`；可在 `.env` 中覆盖 `WEBUI_IMAGE`、`MINIO_IMAGE`。`latest` / `main` 为 CPU 完整镜像，支持 amd64 / arm64；`cuda` 为 amd64 CUDA 镜像。`git-提交短号` 标签可固定应用版本，生产环境还可固定镜像摘要。

需要构建本地修改时：

```bash
WEBUI_IMAGE=nefex-web-ui:local MINIO_IMAGE=nefex-minio:local ./scripts/install-docker.sh --build
```

`--no-build` 仅使用本地已缓存镜像，不拉取、不构建；`--check` 仅检查运行中的服务。自定义 Python 依赖或模型的全量构建文件为 `deploy/Dockerfile.source`，运行 `docker build -f deploy/Dockerfile.source -t nefex-web-ui:custom .`。默认构建复用的运行环境必须与 `backend/requirements.txt` 对应版本一致；更改 Python 依赖时应使用全量构建。

GitHub Actions 在 main、版本标签和手动触发时构建并发布应用及 MinIO，多架构摘要合并前会运行后端回归测试。默认安装不会使用原始 Open WebUI 应用成品，因为它仍包含本分支已经移除的接入。

生产备份应同时保留 `.env`、PostgreSQL 的 `pg_dump`、MinIO 数据、应用数据卷。Redis 与 SearXNG 数据卷也可一并备份。`docker compose down` 保留数据；`docker compose down -v` 会永久删除该项目的全部数据卷。

## 从旧安装迁移

先备份旧数据库、上传文件、向量数据和配置。新的 PostgreSQL 与 MinIO 卷是新存储，安装器不会自动把旧 SQLite 数据库或本地上传目录搬进去；已有数据须使用经过验证的数据库迁移和文件迁移流程，或先保持旧实例可用并在新实例重新导入。

使用已有数据库启动本分支时，会清理已退役的接入配置。旧 Ollama 嵌入设置会回退到当前环境中的嵌入默认值，旧云搜索设置会回退到当前搜索默认值。若旧环境文件仍指定 `RAG_EMBEDDING_ENGINE=ollama`，请先改为空值（本地嵌入）或新的兼容引擎。需要为原有模型预设选择新的 OpenAI 兼容基础模型，并对使用旧嵌入模型的知识库重新索引。历史数据库迁移文件和旧数据库文件名兼容逻辑保留，以便旧数据仍可升级。

## 名称与兼容性

新安装默认 Compose 项目名为 `nefex-web-ui`，界面为 `Nefex Web UI (Open WebUI)`；保留上游名称、图标和许可证。Python 模块、数据库迁移和内部 `open-webui` 服务及卷名保留兼容。已有 `.env` 中的 `COMPOSE_PROJECT_NAME` 不会由安装器改写；保留原值即可继续使用现有数据卷，避免误建空实例。
