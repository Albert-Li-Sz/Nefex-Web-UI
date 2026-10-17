#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT/.env"
CONFIG_FILE=''
pending_file=''
installer_exit=0
init_only=false
build=false
pull=true
check_only=false
gpu=false
gpu_arg=auto
interactive=auto
maintenance=false
original_args=("$@")
source "$ROOT/scripts/lib/docker-common.sh"

usage() {
  cat <<'EOF'
Nefex Web UI Docker 安装
Usage: ./scripts/install-docker.sh [OPTIONS]
  --interactive      强制交互引导（终端直接运行时默认启用）
  --non-interactive  使用已有配置/默认值，无提示运行，适合自动化
  --init-only        仅生成配置；配合 --interactive 可只运行配置向导
  --env-file PATH    使用指定配置文件
  --build            从源码构建应用和 MinIO
  --no-build         仅使用本地缓存镜像，不拉取或构建
  --gpu              启用 NVIDIA CUDA（需要 Container Toolkit）
  --cpu              使用 CPU 镜像
  --check            只读检查服务，不修改配置
  --update           更新旧安装（默认获取最新正式版）
  --to VERSION       指定更新版本，例如 v0.12.8
  --backup           创建完整配置与数据卷备份
  --rollback PATH    从指定备份恢复配置、数据和镜像
  --install-dir PATH 维护另一个安装目录
  --local            使用当前安装包作为更新来源
  --yes              无交互确认（同 --non-interactive）
  -h, --help         显示帮助

非终端输入自动保持非交互模式。已有配置和数据卷保留；密钥不会显示在摘要中。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interactive) interactive=true ;;
    --non-interactive|--yes) interactive=false ;;
    --init-only) init_only=true ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo 'Missing --env-file path' >&2; exit 2; }
      ENV_FILE="$2"; shift ;;
    --build) build=true; pull=false ;;
    --no-build) build=false; pull=false ;;
    --gpu) gpu_arg=true ;;
    --cpu) gpu_arg=false ;;
    --check) check_only=true ;;
    --update|--backup|--list-backups|--local) maintenance=true ;;
    --rollback|--to|--install-dir|--backup-dir|--wait-timeout)
      [[ $# -ge 2 ]] || { echo "Missing $1 value" >&2; exit 2; }
      maintenance=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [[ "$maintenance" == true ]]; then
  exec bash "$ROOT/scripts/update-docker.sh" "${original_args[@]}"
fi

[[ "$ENV_FILE" == /* ]] || ENV_FILE="$PWD/$ENV_FILE"
command -v docker >/dev/null || { echo '请先安装 Docker Engine/Desktop 和 Compose v2。' >&2; exit 1; }
docker compose version >/dev/null
if [[ "$interactive" == auto ]]; then
  interactive=false
  if [[ -t 0 && -t 1 && "$init_only" == false && "$check_only" == false ]]; then interactive=true; fi
fi
# A health check must never become an installation wizard.
[[ "$check_only" == false ]] || interactive=false

cleanup() {
  [[ -z "$CONFIG_FILE" ]] || rm -f -- "$CONFIG_FILE"
  [[ -z "$pending_file" ]] || rm -f -- "$pending_file"
  release_lock
}
trap 'installer_exit=$?; cleanup; exit "$installer_exit"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
umask 077
if [[ "$check_only" == true ]]; then
  [[ -f "$ENV_FILE" ]] || { echo '未找到已有配置，请先安装。' >&2; exit 2; }
  case "$(read_env NEFEX_INSTALL_GPU "$ENV_FILE")" in true) gpu=true ;; esac
  [[ "$gpu_arg" == auto ]] || gpu="$gpu_arg"
  export WEBUI_ENV_FILE="$ENV_FILE"
  compose=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -f "$ROOT/docker-compose.yaml")
  if [[ "$gpu" == true ]]; then compose+=(-f "$ROOT/docker-compose.gpu.yaml"); fi
  "${compose[@]}" config --quiet
  "${compose[@]}" exec -T open-webui python check_stack.py
  "${compose[@]}" ps -a
  exit 0
fi
if [[ "$interactive" == true && -t 0 && -t 1 && -f "$ENV_FILE" && " ${original_args[*]:-} " != *' --interactive '* ]]; then
  echo '检测到已有配置：1 安装/调整配置；2 更新；3 只读检查；4 备份；5 列出备份；6 回滚；0 退出'
  printf '选择操作 [1]: '
  IFS= read -r selection || exit 130
  case "${selection:-1}" in
    1) ;;
    2) exec bash "$ROOT/scripts/update-docker.sh" --update --interactive --env-file "$ENV_FILE" ;;
    3) exec bash "$ROOT/scripts/install-docker.sh" --check --env-file "$ENV_FILE" ;;
    4) exec bash "$ROOT/scripts/update-docker.sh" --backup --interactive --env-file "$ENV_FILE" ;;
    5) exec bash "$ROOT/scripts/update-docker.sh" --list-backups --interactive --env-file "$ENV_FILE" ;;
    6)
      printf '备份目录: '
      IFS= read -r snapshot || exit 130
      [[ -n "$snapshot" ]] || exit 130
      exec bash "$ROOT/scripts/update-docker.sh" --rollback "$snapshot" --interactive --env-file "$ENV_FILE" ;;
    *) exit 130 ;;
  esac
fi
acquire_lock "$ENV_FILE"
CONFIG_FILE=$(mktemp "${ENV_FILE}.XXXXXX")
if [[ -f "$ENV_FILE" ]]; then cp "$ENV_FILE" "$CONFIG_FILE"; else cp "$ROOT/.env.example" "$CONFIG_FILE"; fi

cancel_input() { echo; echo '已取消，原配置未改动。' >&2; exit 130; }
ask() {
  printf '%s [%s]: ' "$1" "$2"
  IFS= read -r REPLY || cancel_input
  REPLY=${REPLY:-$2}
}
ask_secret() {
  printf '%s（隐藏输入；Enter %s，输入 - 清空）: ' "$1" "${3:-保留或稍后配置}"
  IFS= read -r -s REPLY || cancel_input
  echo
  [[ -n "$REPLY" ]] || REPLY="$2"
  [[ "$REPLY" != '-' ]] || REPLY=''
}
ask_choice() {
  while true; do
    ask "$1" "$2"
    case ",$3," in *",$REPLY,"*) return ;; esac
    echo '请输入列出的选项编号。'
  done
}
ask_url() {
  while true; do
    ask "$1" "$2"
    if [[ "$REPLY" =~ ^https?://[^/[:space:]]+(/[^[:space:]]*)?$ ]]; then return; fi
    echo '请输入完整的 http:// 或 https:// 地址。'
  done
}

ask_origin() {
  while true; do
    ask "$1" "$2"
    if [[ "$REPLY" =~ ^https?://[^/[:space:]?#]+/?$ ]]; then REPLY=${REPLY%/}; return; fi
    echo '访问 URL 请只填写协议、域名/IP 和端口，不包含路径、查询或片段。'
  done
}

case "$(read_env NEFEX_INSTALL_GPU)" in true) gpu=true ;; esac
[[ "$gpu_arg" == auto ]] || gpu="$gpu_arg"

if [[ "$interactive" == true ]]; then
  echo 'Nefex Web UI 交互式安装（共 6 步）'
  echo "配置文件：$ENV_FILE"
  echo '已有后台保存的应用设置优先于 .env；此向导不会重置数据库或现有账户。'
  echo
  echo '[1/6] 运行环境与镜像'
  runtime_default=1; [[ "$gpu" == false ]] || runtime_default=2
  ask_choice '运行环境：1 CPU；2 NVIDIA CUDA' "$runtime_default" '1,2'
  gpu=false; [[ "$REPLY" == 1 ]] || gpu=true
  mode_default=1
  if [[ "$build" == true ]]; then mode_default=2; elif [[ "$pull" == false ]]; then mode_default=3; fi
  ask_choice '安装方式：1 拉取发布镜像；2 从源码构建；3 使用本地缓存' "$mode_default" '1,2,3'
  build=false; pull=false
  case "$REPLY" in 1) pull=true ;; 2) build=true ;; esac
  if [[ "$gpu" == true ]]; then
    image_default=$(read_env WEBUI_GPU_IMAGE)
    [[ -n "$image_default" ]] || image_default=$(read_env WEBUI_GPU_IMAGE "$ROOT/.env.example")
    ask 'CUDA 应用镜像' "$image_default"
    [[ -z "$REPLY" ]] || write_env WEBUI_GPU_IMAGE "$REPLY"
  else
    image_default=$(read_env WEBUI_IMAGE)
    [[ -n "$image_default" ]] || image_default=$(read_env WEBUI_IMAGE "$ROOT/.env.example")
    ask 'CPU 应用镜像' "$image_default"
    [[ -z "$REPLY" ]] || write_env WEBUI_IMAGE "$REPLY"
  fi

  echo; echo '[2/6] 访问地址'
  old_port=$(read_env OPEN_WEBUI_PORT)
  while true; do
    ask 'WebUI 端口' "${old_port:-3000}"
    if [[ "$REPLY" =~ ^[1-9][0-9]{0,4}$ ]] && (( REPLY <= 65535 )); then break; fi
    echo '端口必须是 1–65535 的整数。'
  done
  port="$REPLY"; write_env OPEN_WEBUI_PORT "$port"
  ask '监听地址（0.0.0.0 对外开放，127.0.0.1 仅本机）' "$(read_env WEBUI_BIND_ADDRESS)"
  write_env WEBUI_BIND_ADDRESS "${REPLY:-0.0.0.0}"
  url_default=$(read_env WEBUI_URL)
  if [[ -z "$url_default" || "$port" != "$old_port" ]]; then url_default="http://localhost:$port"; fi
  ask_origin '浏览器访问 URL（域名/IP，包含实际端口）' "$url_default"
  write_env WEBUI_URL "$REPLY"
  write_env CORS_ALLOW_ORIGIN "$REPLY"

  echo; echo '[3/6] 模型接口（可安装后在管理后台配置）'
  ask_choice '现在配置 OpenAI 兼容接口？1 稍后配置；2 现在配置' '1' '1,2'
  if [[ "$REPLY" == 2 ]]; then
    model_url=$(read_env OPENAI_API_BASE_URL)
    ask_url '模型 API Base URL' "${model_url:-https://api.openai.com/v1}"
    write_env OPENAI_API_BASE_URL "$REPLY"
    ask_secret '模型 API Key' "$(read_env OPENAI_API_KEY)"
    write_env OPENAI_API_KEY "$REPLY"
  fi

  echo; echo '[4/6] 联网搜索'
  search_default=3
  case "$(read_env WEB_SEARCH_ENGINE)" in ''|searxng) search_default=1 ;; deepseek) search_default=2 ;; esac
  ask_choice '搜索：1 SearXNG（无需密钥）；2 DeepSeek Search/Fetch；3 保持现有设置' "$search_default" '1,2,3'
  case "$REPLY" in
    1) write_env WEB_SEARCH_ENGINE searxng; write_env WEB_LOADER_ENGINE playwright ;;
    2)
      deepseek_url=$(read_env DEEPSEEK_SEARCH_API_BASE_URL)
      ask_url 'DeepSeek API Base URL' "${deepseek_url:-https://api.deepseek.com}"
      write_env DEEPSEEK_SEARCH_API_BASE_URL "$REPLY"
      ask_secret 'DeepSeek 官方 API Key' "$(read_env DEEPSEEK_SEARCH_API_KEY)"
      write_env DEEPSEEK_SEARCH_API_KEY "$REPLY"
      if [[ -n "$REPLY" ]]; then
        write_env WEB_SEARCH_ENGINE deepseek; write_env WEB_LOADER_ENGINE deepseek
      else
        echo '未填写 DeepSeek 密钥，先使用 SearXNG；可安装后在后台切换。'
        write_env WEB_SEARCH_ENGINE searxng; write_env WEB_LOADER_ENGINE playwright
      fi
      ;;
  esac

  echo; echo '[5/6] 管理员账户（已有用户时不会重置密码）'
  ask_choice '首次安装：1 首次打开网页创建；2 预设管理员（推荐）' '2' '1,2'
  if [[ "$REPLY" == 2 ]]; then
    email_default=$(read_env WEBUI_ADMIN_EMAIL)
    while true; do
      ask '管理员邮箱' "${email_default:-admin@nefex.local}"
      if [[ "$REPLY" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then break; fi
      echo '请输入有效的邮箱地址。'
    done
    write_env WEBUI_ADMIN_EMAIL "$REPLY"
    old_password=$(read_env WEBUI_ADMIN_PASSWORD)
    while true; do
      ask_secret '管理员密码（至少 12 字符）' "$old_password" '保留现有密码或自动生成'
      if [[ -z "$REPLY" ]]; then REPLY=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); break; fi
      if [[ -n "$old_password" && "$REPLY" == "$old_password" ]]; then break; fi
      if (( ${#REPLY} >= 12 )); then break; fi
      echo '请使用至少 12 个字符。'
    done
    write_env WEBUI_ADMIN_PASSWORD "$REPLY"
    write_env ENABLE_SIGNUP false
  else
    write_env WEBUI_ADMIN_EMAIL ''; write_env WEBUI_ADMIN_PASSWORD ''; write_env ENABLE_SIGNUP true
  fi

  echo; echo '[6/6] 核对配置'
  echo "运行环境：$(if [[ "$gpu" == true ]]; then echo NVIDIA-CUDA; else echo CPU; fi)"
  echo "镜像方式：$(if [[ "$build" == true ]]; then echo 源码构建; elif [[ "$pull" == true ]]; then echo 拉取发布镜像; else echo 本地缓存; fi)"
  if [[ "$gpu" == true ]]; then echo "应用镜像：$(read_env WEBUI_GPU_IMAGE)"; else echo "应用镜像：$(read_env WEBUI_IMAGE)"; fi
  echo "模型接口：$(read_env OPENAI_API_BASE_URL)"
  if [[ -n "$(read_env OPENAI_API_KEY)" ]]; then echo '模型密钥：已配置（隐藏）'; else echo '模型密钥：稍后配置'; fi
  echo "监听地址：$(read_env WEBUI_BIND_ADDRESS):$(read_env OPEN_WEBUI_PORT)"
  echo "访问地址：$(read_env WEBUI_URL)"
  echo "搜索引擎：$(read_env WEB_SEARCH_ENGINE) / $(read_env WEB_LOADER_ENGINE)"
  if [[ -n "$(read_env WEBUI_ADMIN_EMAIL)" ]]; then
    echo "管理员邮箱：$(read_env WEBUI_ADMIN_EMAIL)；密码仅保存在权限为 600 的配置文件中。"
  else echo '管理员：首次访问网页创建。'; fi
  action='保存配置并开始安装'
  [[ "$init_only" == false ]] || action='仅保存配置'
  ask "确认${action}？输入 y 继续，其余取消" 'N'
  case "$REPLY" in y|Y|yes|YES) ;; *) cancel_input ;; esac
fi

if [[ "$build" == true && ( ! -f "$ROOT/package.json" || ! -d "$ROOT/backend" ) ]]; then
  echo '源码构建需要完整源码包；镜像安装包请选择拉取发布镜像。' >&2
  exit 2
fi

if [[ "$build" == true && -f "$ROOT/VERSION" ]]; then
  source_version=$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/package.json" | head -1)
  [[ "$source_version" == "$(tr -d '\r\n' < "$ROOT/VERSION")" ]] || {
    echo '部署文件与源码版本不同；请先同步完整源码，再选择 --build。' >&2; exit 2;
  }
fi

if [[ "$interactive" == true || "$gpu_arg" != auto ]]; then write_env NEFEX_INSTALL_GPU "$gpu"; fi
for key in POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_PASSWORD WEBUI_SECRET_KEY SEARXNG_SECRET; do
  if [[ -z "$(read_env "$key")" ]]; then
    write_env "$key" "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  fi
done

set_compose() {
  export WEBUI_ENV_FILE="$1"
  compose=(docker compose --project-directory "$ROOT" --env-file "$1" -f "$ROOT/docker-compose.yaml")
  if [[ "$gpu" == true ]]; then compose+=(-f "$ROOT/docker-compose.gpu.yaml"); fi
}
set_compose "$CONFIG_FILE"
"${compose[@]}" config --quiet
access_url=$(read_env WEBUI_URL)
[[ -n "$access_url" ]] || access_url="http://localhost:$(read_env OPEN_WEBUI_PORT)"
mv "$CONFIG_FILE" "$ENV_FILE"
CONFIG_FILE=''
chmod 600 "$ENV_FILE"
set_compose "$ENV_FILE"

if [[ "$init_only" == true ]]; then
  echo "Configuration ready: $ENV_FILE"
  echo '配置已保存。再次运行安装器即可启动服务。'
  exit 0
fi

docker info >/dev/null
if [[ "$check_only" != true ]]; then
  if [[ "$build" == true ]]; then
    "${compose[@]}" pull postgres redis searxng
    if [[ "$gpu" == true ]]; then "${compose[@]}" pull minio-init; fi
    "${compose[@]}" build minio
    "${compose[@]}" build open-webui
  elif [[ "$pull" == true ]]; then
    "${compose[@]}" pull
  fi
  if ! "${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 600; then
    "${compose[@]}" ps -a
    "${compose[@]}" logs --tail 60
    exit 1
  fi
fi

"${compose[@]}" exec -T open-webui python check_stack.py
"${compose[@]}" ps -a
echo 'Installation checks passed.'
echo "访问地址：$access_url"
echo "配置文件：${ENV_FILE}（管理员密码和服务密钥仅保存在该文件中）"
