# Nefex Web UI Docker 完整安装

Nefex Web UI 通过 OpenAI 兼容接口接入模型。Ollama 原生接口、连接管理、模型拉取、嵌入引擎和云搜索已移除。

## 一键安装

需要 Docker Engine / Docker Desktop，以及支持 `up --wait` 的 Compose v2。支持 Linux amd64 / arm64 与 macOS Docker Desktop；默认安装只拉取镜像，无需 Node.js、Python 或 Go。请预留至少 20 GB 磁盘空间，运行建议分配至少 4 GB 内存。只有使用 `--build` 源码构建时才需要至少 8 GB 内存；默认前端构建堆上限为 6 GB，可通过 `BUILD_NODE_OPTIONS=--max-old-space-size=8192` 调整。

```bash
curl -fL https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/latest/download/install.sh -o /tmp/nefex-install.sh
bash /tmp/nefex-install.sh --install-dir ./nefex-web-ui
```

独立入口下载最新正式版 Docker 安装包和 `SHA256SUMS`，校验通过后解压到空目录，再进入中文安装向导。也可从 Release 手动解压 Docker 安装包，或克隆完整仓库后执行 `./scripts/install-docker.sh`。源码构建需要完整仓库或源码包。

脚本创建权限为 `600` 的 `.env`，为数据库、Redis、MinIO、应用会话和搜索自动生成独立随机密钥，拉取 Nefex Web UI、MinIO 和其他上游镜像，启动服务，等待健康检查，然后执行真实服务检查。再次执行会保留配置、密钥和数据卷。

### 交互安装向导

向导按 6 步完成配置：

1. 选择 CPU 或 NVIDIA CUDA，以及拉取发布镜像、源码构建或使用本地缓存。CUDA 需要 NVIDIA Container Toolkit；轻量 Docker 安装包选择源码构建时会提示改用完整源码包。
2. 设置 WebUI 端口、监听地址、实际访问 URL。CORS 地址随访问 URL 一起保存；使用反向代理时填写公开的 HTTPS 地址。
3. 可选填写 OpenAI 兼容模型地址和 API Key，也可安装后在管理后台配置。
4. 选择无需密钥的 SearXNG，或填写 DeepSeek 官方密钥使用 Search/Fetch。未填写 DeepSeek 密钥时先使用 SearXNG。
5. 预设管理员邮箱和密码（推荐），或选择首次打开网页创建。管理员密码为空时会保留已有密码或生成随机密码；预设管理员时关闭开放注册。
6. 核对配置摘要并确认。密钥不显示在摘要中；确认前退出、输入结束或取消，都不会覆盖原有 `.env`，也不会启动容器。

已有 `.env` 会作为默认值，原有服务密钥保留。写入前先验证 Compose 配置，验证失败时保留原配置。API Key 隐藏输入，Enter 保留已有值，输入 `-` 清空；所有密钥只保存在权限为 `600` 的配置文件中。自动生成的管理员密码可在本机该文件的 `WEBUI_ADMIN_PASSWORD` 项查看。

已有用户的账户和密码不会被向导重置；已在管理后台保存的应用配置优先于环境变量。CUDA 选择会保存为 `NEFEX_INSTALL_GPU`，再次运行仍使用同一模式；`--cpu` / `--gpu` 可显式切换。更换 Compose 项目名会使用不同数据卷，向导不会修改已有项目名。

```bash
# 强制进入引导，仅保存配置
./scripts/install-docker.sh --interactive --init-only
# 自动化：按已有 .env 或默认值运行，不显示提示
./scripts/install-docker.sh --non-interactive
# 指定独立配置文件
./scripts/install-docker.sh --interactive --env-file ./production.env
```

首次安装在终端直接运行默认进入引导；已有配置时先显示安装/调整配置、更新、检查、备份、列出备份和回滚菜单。`--interactive` 显式进入六步配置向导。非终端安装保持非交互模式，`--init-only` 和 `--check` 默认也不显示提示。`--check` 始终只检查服务，配合 `--interactive` 也不会打开安装引导或改写配置。

应用默认地址为 <http://localhost:3000>，安装结束会显示实际地址。模型接口可在「管理员设置 → 连接」填写。也可先生成配置再编辑：

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

### DeepSeek Search / Fetch

支持 [DeepSeek 官方 Search 与 Fetch API](https://api-docs.deepseek.com/zh-cn/guides/search/)。在「管理员设置 → 联网搜索」中选择 **DeepSeek Search**，填写 DeepSeek 官方 API Key 并保存；API 地址默认 `https://api.deepseek.com`，也兼容以 `/v1` 结尾的地址。该密钥独立于聊天模型连接的密钥。

网页加载器可单独选择 **DeepSeek Fetch**，与搜索共用地址和密钥，返回的 Markdown 正文可继续进入本地嵌入、知识库检索和引用流程。也可以继续使用 Playwright 抓取，或启用「随搜索结果返回网页正文」和「跳过网页加载器」，直接使用 Search 返回的正文（无正文时使用摘要）。

新安装也可通过 `.env` 配置后运行安装器：

```dotenv
WEB_SEARCH_ENGINE=deepseek
WEB_LOADER_ENGINE=deepseek
DEEPSEEK_SEARCH_API_BASE_URL=https://api.deepseek.com
DEEPSEEK_SEARCH_API_KEY=你的官方API密钥
DEEPSEEK_SEARCH_NEED_CONTENT=false
```

搜索词支持 DeepSeek 的查询运算符，长度为 1–128 个字符；返回数量限制在 1–50。域名过滤继续生效。临时超时、限流和服务端错误会有限重试；密钥或余额错误会明确提示。DeepSeek 按账户限制请求频率，单次 Fetch 加载任务最多每秒发起 5 个网页请求；账户在其他应用或多个实例中的用量仍会共享限额。

未填写 DeepSeek 密钥时保持默认 `searxng` / `playwright` 即可。切换为 DeepSeek 后，接口失败会显示错误，方便检查凭据和余额。SearXNG 服务仍随完整 Docker 安装部署，安装检查继续验证它的可用性。

## 管理、更新与备份

```bash
docker compose ps -a
docker compose logs -f open-webui
docker compose logs --tail 100 searxng
# 更新到最新正式版
./scripts/install-docker.sh --update
# 固定目标版本
./scripts/install-docker.sh --update --to v0.12.7
# 自动化执行，不询问确认
./scripts/install-docker.sh --update --non-interactive
# 停止服务，保留全部数据
docker compose down
```

更新会先下载并校验目标发布包、拉取并核对目标镜像版本；这时原服务继续运行。确认后暂停服务，为配置和全部项目数据卷创建一致快照，再替换安装文件并启动新版本。容器健康检查和完整服务检查都通过才报告成功；任一步失败会恢复原配置、数据与镜像，并以失败状态退出。

`.env` 中的密钥、自定义配置和端口保留；后台保存的模型、搜索及用户配置仍保存在 PostgreSQL。未显式写入项目名的旧安装会从原容器识别名称并固定，继续使用原数据卷。更新只切换应用镜像，PostgreSQL、MinIO、Redis 和 SearXNG 固定为原运行镜像摘要，基础服务升级应另行安排。CPU/CUDA 默认沿用原模式。

更新和备份会产生一段停服时间，主要取决于数据量和磁盘速度。脚本检查备份空间、拒绝共享或外部卷，并为同一个配置文件设置维护锁；终止进程留下的失效锁可自动恢复。不会删除项目数据卷。

### 更新 v0.12.4 / v0.12.5

旧安装器没有 `--update` 时，在任意目录下载最新入口，将 `--install-dir` 指向已有 `.env` 和 `docker-compose.yaml` 的目录：

```bash
curl -fL https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/latest/download/install.sh -o /tmp/nefex-install.sh
bash /tmp/nefex-install.sh --update --install-dir /opt/nefex-web-ui
```

也可将新版 Docker 安装包解压到另一个目录，使用其中的脚本维护原安装。此方式无需重复下载安装包，仍会拉取所需应用镜像：

```bash
./scripts/install-docker.sh --update --local --install-dir /opt/nefex-web-ui
```

支持现有 Nefex PostgreSQL/MinIO 完整 Docker 服务栈，包括旧项目名。原始 Open WebUI SQLite、本地文件存储、共享数据库/外部卷等布局不能直接使用此更新流程，迁移说明见下一节。部署更新会替换安装清单内的 Compose、SearXNG 配置和安装文件；有定制修改时应先核对，原文件会保存在快照中。

### 完整备份与回滚

```bash
./scripts/install-docker.sh --backup
./scripts/install-docker.sh --list-backups
./scripts/install-docker.sh --rollback /opt/nefex-web-ui/backups/备份目录
# 备份到另一块磁盘；自动化操作另加 --non-interactive
./scripts/install-docker.sh --backup --backup-dir /mnt/backup/nefex
```

默认快照保存在原安装目录的 `backups/`，每个目录权限为 `700`，包含 `.env`、安装配置、固定镜像引用、校验文件及全部项目数据卷（数据库、文件、向量、缓存与匿名配置卷）。快照包含密钥和用户数据，应保存到受控的存储位置；脚本不会自动清理历史快照。

回滚先校验备份，并为当前状态另建完整快照，然后恢复所选快照的配置、镜像和数据。快照限定原安装路径与项目名，用于原地恢复。恢复后的数据回到备份时点，后续新增或修改的数据可在操作前创建的快照中保留。直接用 `--to` 降级会被拒绝，数据库降级应恢复对应版本快照。恢复旧版后，安装目录里的脚本也会回到旧版；再次更新或回滚可使用另行下载的新版入口。

建议将完整快照复制到异地并定期演练恢复。只备份 PostgreSQL 而不保留 MinIO 文件，会造成数据库记录与附件不一致。

### 镜像与源码构建

新安装默认应用镜像固定为 `ghcr.io/albert-li-sz/nefex-web-ui:v0.12.7`，CUDA 镜像为 `ghcr.io/albert-li-sz/nefex-web-ui:v0.12.7-cuda`，MinIO 为 `ghcr.io/albert-li-sz/nefex-web-ui-minio:RELEASE.2025-10-15T17-29-55Z`；可在 `.env` 中覆盖 `WEBUI_IMAGE`、`MINIO_IMAGE`。`latest` / `main` 为 CPU 完整镜像，支持 amd64 / arm64；`cuda` 为 amd64 CUDA 镜像。`git-提交短号` 标签可固定应用版本，生产环境还可固定镜像摘要。

需要构建本地修改时：

```bash
WEBUI_IMAGE=nefex-web-ui:local MINIO_IMAGE=nefex-minio:local ./scripts/install-docker.sh --build
```

`--no-build` 仅使用本地已缓存镜像，不拉取、不构建；`--check` 仅检查运行中的服务。自定义 Python 依赖或模型的全量构建文件为 `deploy/Dockerfile.source`，运行 `docker build -f deploy/Dockerfile.source -t nefex-web-ui:custom .`。默认构建复用的运行环境必须与 `backend/requirements.txt` 对应版本一致；更改 Python 依赖时应使用全量构建。

部署更新只更新安装清单内的运行配置与脚本，不改写现有应用源码。当 `VERSION` 与 `package.json` 不一致时会拒绝 `--build`；需要源码构建时请使用同版本的完整源码包或 Git 标签。

GitHub Actions 在版本标签和手动触发时构建应用镜像，多架构摘要合并前运行安装器与后端回归测试。Linux/macOS 的安装器回归在 main 和 PR 独立执行；MinIO 源码未变化且已发布镜像可用时直接复用。默认安装不会使用原始 Open WebUI 应用成品，因为它仍包含本分支已经移除的接入。

`docker compose down` 保留数据；`docker compose down -v` 会永久删除该项目的全部数据卷。

## 从旧安装迁移

先备份旧数据库、上传文件、向量数据和配置。新的 PostgreSQL 与 MinIO 卷是新存储，安装器不会自动把旧 SQLite 数据库或本地上传目录搬进去；已有数据须使用经过验证的数据库迁移和文件迁移流程，或先保持旧实例可用并在新实例重新导入。

使用已有数据库启动本分支时，会清理已退役的接入配置。旧 Ollama 嵌入设置会回退到当前环境中的嵌入默认值，旧云搜索设置会回退到当前搜索默认值。若旧环境文件仍指定 `RAG_EMBEDDING_ENGINE=ollama`，请先改为空值（本地嵌入）或新的兼容引擎。需要为原有模型预设选择新的 OpenAI 兼容基础模型，并对使用旧嵌入模型的知识库重新索引。历史数据库迁移文件和旧数据库文件名兼容逻辑保留，以便旧数据仍可升级。

## 名称与兼容性

新安装默认 Compose 项目名为 `nefex-web-ui`，界面为 `Nefex Web UI (Open WebUI)`；保留上游名称、图标和许可证。Python 模块、数据库迁移和内部 `open-webui` 服务及卷名保留兼容。已有 `.env` 中的 `COMPOSE_PROJECT_NAME` 不会由安装器改写；保留原值即可继续使用现有数据卷，避免误建空实例。

构建缓存仅用于加速：CPU 缓存导出超过 2 分钟或失败时继续发布；CUDA 不导出大体积运行环境缓存，直接复用官方镜像层。镜像构建和后端回归测试仍必须通过。
