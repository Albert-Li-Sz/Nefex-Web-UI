#!/usr/bin/env bash
# In-place maintenance for the PostgreSQL/MinIO Nefex Docker distribution.
set -euo pipefail
DIST_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ROOT="$DIST_ROOT"
ENV_FILE=''
CONFIG_FILE=''
pending_file=''
# shellcheck source=scripts/lib/docker-common.sh
source "$DIST_ROOT/scripts/lib/docker-common.sh"
# shellcheck source=install.sh
source "$DIST_ROOT/install.sh"
action=update
target=latest
local_distribution=false
interactive=auto
backup_base=''
restore_path=''
wait_timeout=600
gpu=auto
stage=''
recovery=''
transaction=idle
project=''
helper=''
backup=''
managed_manifest="$DIST_ROOT/install-files.txt"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update) action=update ;;
    --backup) action=backup ;;
    --list-backups) action=list ;;
    --check) action=check ;;
    --rollback|--to|--install-dir|--env-file|--backup-dir|--wait-timeout)
      [[ $# -ge 2 ]] || { echo "Missing $1 value" >&2; exit 2; }
      case "$1" in
        --rollback) action=rollback; restore_path="$2" ;;
        --to) target="$2" ;;
        --install-dir) ROOT="$2" ;;
        --env-file) ENV_FILE="$2" ;;
        --backup-dir) backup_base="$2" ;;
        --wait-timeout) wait_timeout="$2" ;;
      esac
      shift ;;
    --local) local_distribution=true ;;
    --non-interactive|--yes) interactive=false ;;
    --interactive) interactive=true ;;
    --gpu) gpu=true ;;
    --cpu) gpu=false ;;
    *) echo "不支持的维护参数：$1" >&2; exit 2 ;;
  esac
  shift
done
[[ -d "$ROOT" ]] || { echo '安装目录不存在。' >&2; exit 2; }
ROOT=$(cd -- "$ROOT" && pwd -P)
[[ -n "$ENV_FILE" ]] || ENV_FILE="$ROOT/.env"
[[ "$ENV_FILE" == /* ]] || ENV_FILE="$PWD/$ENV_FILE"
[[ -n "$backup_base" ]] || backup_base="$ROOT/backups"
[[ "$backup_base" == /* ]] || backup_base="$PWD/$backup_base"
[[ "$ROOT$ENV_FILE$backup_base" != *','* && "$ROOT$ENV_FILE$backup_base" != *$'\n'* && "$ROOT$ENV_FILE$backup_base" != *$'\t'* ]] || {
  echo '维护路径不能包含逗号、制表符或换行。' >&2; exit 2;
}
[[ "$wait_timeout" =~ ^[1-9][0-9]{0,3}$ ]] || { echo '等待时间必须是 1–9999 秒。' >&2; exit 2; }
if [[ "$interactive" == auto ]]; then
  if [[ -t 0 && -t 1 ]]; then interactive=true; else
    echo '非终端维护请明确添加 --non-interactive。' >&2; exit 2
  fi
fi
umask 077

finish() {
  local result=$? repair=0
  trap - EXIT INT TERM
  set +e
  if (( result != 0 )); then
    if [[ "$transaction" == applied && -n "$recovery" ]]; then
      echo '更新/恢复失败，正在从更新前快照恢复……' >&2
      restore_snapshot "$recovery"
      repair=$?
      if (( repair == 0 )); then echo '已恢复原配置、数据和镜像。' >&2;
      else echo "自动恢复失败，请保留备份并检查：$recovery" >&2; fi
    elif [[ "$transaction" == stopped && -n "$recovery" ]]; then
      resume_snapshot "$recovery"
      repair=$?
      (( repair == 0 )) || echo "原服务恢复启动失败，配置备份：$recovery" >&2
    fi
  fi
  [[ -z "$CONFIG_FILE" ]] || rm -f -- "$CONFIG_FILE"
  [[ -z "$pending_file" ]] || rm -f -- "$pending_file"
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  release_lock
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v docker >/dev/null || { echo '需要 Docker 和 Compose v2。' >&2; exit 2; }
docker compose version >/dev/null
[[ -f "$ENV_FILE" && -f "$ROOT/docker-compose.yaml" ]] || { echo '未找到旧安装的 .env 和 docker-compose.yaml。' >&2; exit 2; }
export WEBUI_ENV_FILE="$ENV_FILE"

owned_volume() {
  local volume="$1" expected_project="${2:-$project}" owner labels consumer
  owner=$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$volume")
  if [[ "$owner" != "$expected_project" ]]; then
    # Official images may declare anonymous volumes (SearXNG: /etc/searxng).
    labels=$(docker volume inspect --format '{{json .Labels}}' "$volume")
    [[ "$labels" == *'"com.docker.volume.anonymous":'* ]] || return 1
  fi
  while IFS= read -r consumer; do
    [[ -n "$consumer" ]] || continue
    [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$consumer")" == "$expected_project" ]] || return 1
  done < <(docker ps -aq --filter "volume=$volume")
}
container_env() {
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" |
    awk -v key="$2" 'index($0,key "=")==1 {print substr($0,length(key)+2)}'
}
deployment_matches() {
  # A Compose working_dir label is historical metadata, not a stable identity:
  # relocated installs, symlinks and remote daemons can legitimately differ.
  # Before accepting a different path, prove the selected configuration belongs
  # to all five services and to the exact named volumes the new template will use.
  local candidate="$1" candidate_app="$2" service key destination id expected volume mapping value
  [[ "$candidate" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || return 1
  for mapping in \
    'open-webui WEBUI_SECRET_KEY /app/backend/data' \
    'postgres POSTGRES_PASSWORD /var/lib/postgresql/data' \
    'redis REDIS_PASSWORD /data' 'minio MINIO_ROOT_PASSWORD /data' \
    'searxng SEARXNG_SECRET /var/cache/searxng'; do
    read -r service key destination <<< "$mapping"
    id=$(docker ps -aq --filter "label=com.docker.compose.project=$candidate" --filter "label=com.docker.compose.service=$service")
    [[ -n "$id" && "$id" != *$'\n'* ]] || return 1
    if [[ "$service" == open-webui ]]; then
      [[ "$(docker inspect --format '{{.Id}}' "$id")" == "$(docker inspect --format '{{.Id}}' "$candidate_app")" ]] || return 1
    fi
    [[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")" == "$candidate" ]] || return 1
    expected=$(read_env "$key" "$ENV_FILE")
    [[ -n "$expected" && "$(container_env "$id" "$key")" == "$expected" ]] || return 1
    volume=$(docker inspect --format "{{range .Mounts}}{{if and (eq .Type \"volume\") (eq .Destination \"$destination\")}}{{println .Name}}{{end}}{{end}}" "$id")
    [[ "$volume" == "${candidate}_$service" ]] || return 1
    [[ "$(docker volume inspect --format '{{index .Labels "com.docker.compose.volume"}}' "$volume")" == "$service" ]] || return 1
    owned_volume "$volume" "$candidate" || return 1
  done
  for mapping in 'postgres POSTGRES_USER webui' 'postgres POSTGRES_DB webui' 'minio MINIO_ROOT_USER webui-admin'; do
    read -r service key expected <<< "$mapping"
    id=$(docker ps -aq --filter "label=com.docker.compose.project=$candidate" --filter "label=com.docker.compose.service=$service")
    value=$(read_env "$key" "$ENV_FILE")
    [[ -z "$value" ]] || expected="$value"
    [[ "$(container_env "$id" "$key")" == "$expected" ]] || return 1
  done
}

project=${COMPOSE_PROJECT_NAME:-$(read_env COMPOSE_PROJECT_NAME "$ENV_FILE")}
if [[ -z "$project" ]]; then
  # Retain legacy names inferred from the old working directory (for example olm).
  candidates=''
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    working=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$cid")
    if [[ -d "$working" ]]; then working=$(cd -- "$working" && pwd -P); fi
    if [[ "$working" == "$ROOT" ]]; then
      name=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$cid")
      candidates="$candidates$name"$'\n'
    fi
  done < <(docker ps -aq --filter label=com.docker.compose.service=open-webui)
  project=$(printf '%s' "$candidates" | sed '/^$/d' | sort -u)
  if [[ -z "$project" ]]; then
    while IFS= read -r cid; do
      [[ -n "$cid" ]] || continue
      name=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$cid")
      if deployment_matches "$name" "$cid"; then candidates="$candidates$name"$'\n'; fi
    done < <(docker ps -aq --filter label=com.docker.compose.service=open-webui)
    project=$(printf '%s' "$candidates" | sed '/^$/d' | sort -u)
  fi
fi
[[ -n "$project" ]] || { echo '未找到与目录、密钥及数据卷匹配的旧项目，请检查 .env 和 COMPOSE_PROJECT_NAME。' >&2; exit 2; }
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo '无法唯一确定旧 Compose 项目名，请检查 COMPOSE_PROJECT_NAME。' >&2; exit 2; }
compose=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -p "$project" -f "$ROOT/docker-compose.yaml")
existing_gpu=false
case "$(read_env NEFEX_INSTALL_GPU "$ENV_FILE")" in true) existing_gpu=true ;; esac
app_id=$("${compose[@]}" ps -aq open-webui)
[[ -n "$app_id" && "$app_id" != *$'\n'* ]] || { echo '未找到唯一的旧应用容器，请先完成安装或检查项目名。' >&2; exit 2; }
[[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$app_id")" == "$project" ]] || {
  echo '旧应用容器的项目标签与 COMPOSE_PROJECT_NAME 不一致。' >&2; exit 2;
}
app_working=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$app_id")
if [[ -d "$app_working" ]]; then app_working=$(cd -- "$app_working" && pwd -P); fi
if [[ "$app_working" != "$ROOT" ]]; then
  deployment_matches "$project" "$app_id" || {
    printf '项目 %s 记录的原安装路径：%s\n当前安装路径：%s\n' "$project" "$app_working" "$ROOT" >&2
    echo '路径不同且密钥或数据卷不匹配，无法确认是同一安装；请检查 .env 和 COMPOSE_PROJECT_NAME。' >&2; exit 2;
  }
  printf '已通过服务配置与数据卷核对，沿用项目 %s（原路径：%s）。\n' "$project" "$app_working"
fi
if [[ "$(docker inspect --format '{{if .HostConfig.DeviceRequests}}true{{else}}false{{end}}' "$app_id")" == true ]]; then existing_gpu=true; fi
if [[ "$existing_gpu" == true ]]; then compose+=(-f "$ROOT/docker-compose.gpu.yaml"); fi
[[ "$gpu" != auto ]] || gpu="$existing_gpu"
"${compose[@]}" config --quiet
if [[ "$action" == check ]]; then
  "${compose[@]}" exec -T open-webui python check_stack.py
  "${compose[@]}" ps -a
  exit 0
fi
if [[ "$action" == list ]]; then
  if [[ -d "$backup_base" ]]; then
    for path in "$backup_base"/*; do [[ ! -f "$path/complete" ]] || printf '%s\n' "$path"; done
  fi
  exit 0
fi
acquire_lock "$ENV_FILE"
docker info >/dev/null
for key in POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_PASSWORD WEBUI_SECRET_KEY SEARXNG_SECRET; do
  [[ -n "$(read_env "$key" "$ENV_FILE")" ]] || { echo "旧配置缺少 ${key}，维护不会生成新密钥覆盖旧数据。" >&2; exit 2; }
done
for service in postgres minio redis searxng minio-init; do
  id=$("${compose[@]}" ps -aq "$service")
  [[ -n "$id" && "$id" != *$'\n'* ]] || { echo "未找到 ${service}；只支持现有 Nefex PostgreSQL/MinIO 完整服务栈。" >&2; exit 2; }
done
helper_id=$("${compose[@]}" ps -aq minio-init)
helper=$(docker inspect --format '{{.Image}}' "$helper_id")
pin_image() {
  docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}' "$1"
}
helper=$(pin_image "$helper")
docker run --rm --pull never --network none --entrypoint sh "$helper" -ec 'command -v tar >/dev/null; command -v gzip >/dev/null'
app_image=$(docker inspect --format '{{.Image}}' "$app_id")
current=$(docker run --rm --pull never --network none --entrypoint python "$app_image" -c 'import json;print(json.load(open("/app/package.json"))["version"])')
[[ "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo '无法识别旧应用版本。' >&2; exit 2; }

confirm() {
  if [[ "$interactive" == true ]]; then
    printf '确认执行？输入 y 继续，其余取消 [N]: '
    IFS= read -r answer || return 130
    case "$answer" in y|Y|yes|YES) ;; *) return 130 ;; esac
  fi
}
validate_file_list() {
  local file
  [[ -f "$1/install-files.txt" ]] || return 1
  while IFS= read -r file; do
    [[ "$file" =~ ^[a-zA-Z0-9_./-]+$ && "/$file/" != *'/../'* && "$file" != /* && -f "$1/$file" && ! -L "$1/$file" ]] || return 1
  done < "$1/install-files.txt"
}
rollback_compose() {
  rollback_command=(docker compose --project-directory "$ROOT" -p "$project" -f "$1/compose.yaml")
}
resume_snapshot() (
  set -e
  local snapshot="$1" services=() service
  while IFS= read -r service; do [[ -z "$service" ]] || services+=("$service"); done < "$snapshot/running.services"
  if (( ${#services[@]} )); then
    rollback_compose "$snapshot"
    "${rollback_command[@]}" up -d --no-build --pull never --wait --wait-timeout 600 "${services[@]}"
  fi
)
validate_snapshot() {
  local snapshot="$1" file expected actual volume logical archive matches
  local required=(format project.name root.path helper.image environment.env files.present files.absent configuration.tar.gz images.tsv running.services volumes.tsv compose.yaml)
  [[ "$snapshot" != *','* && "$snapshot" != *$'\n'* ]] || return 1
  [[ -d "$snapshot" && -f "$snapshot/SHA256SUMS" ]] || { echo '备份不完整。' >&2; return 1; }
  if [[ "${2:-true}" != false && ! -f "$snapshot/complete" ]]; then echo '备份未完成。' >&2; return 1; fi
  for file in "${required[@]}"; do
    [[ -f "$snapshot/$file" && ! -L "$snapshot/$file" ]] || { echo "备份缺少文件：$file" >&2; return 1; }
  done
  [[ "$(cat "$snapshot/format")" == 1 && "$(cat "$snapshot/project.name")" == "$project" && "$(cat "$snapshot/root.path")" == "$ROOT" ]] || {
    echo '备份格式、项目名或安装路径不匹配。' >&2; return 1;
  }
  while IFS=$'\t' read -r volume logical archive; do
    [[ "$volume" =~ ^[a-zA-Z0-9_.-]+$ && "$logical" =~ ^[a-zA-Z0-9_.-]+$ && "$archive" =~ ^volume-[0-9]+\.tar\.gz$ ]] || return 1
    required+=("$archive")
  done < "$snapshot/volumes.tsv"
  (( ${#required[@]} >= 17 )) || { echo '备份缺少完整的数据卷记录。' >&2; return 1; }
  for file in "${required[@]}"; do
    matches=$(awk -v name="$file" '$2 == name {n++} END {print n+0}' "$snapshot/SHA256SUMS")
    [[ "$matches" == 1 ]] || { echo "备份校验清单缺少或重复条目：$file" >&2; return 1; }
  done
  [[ "$(awk 'END {print NR}' "$snapshot/SHA256SUMS")" -eq "${#required[@]}" ]] || { echo '备份校验清单条目不匹配。' >&2; return 1; }
  while read -r expected file || [[ -n "${expected:-}${file:-}" ]]; do
    [[ "$expected" =~ ^[0-9a-f]{64}$ && "$file" =~ ^[a-zA-Z0-9_.-]+$ && -f "$snapshot/$file" && ! -L "$snapshot/$file" ]] || return 1
    actual=$(nefex_hash "$snapshot/$file")
    [[ "$actual" == "$expected" ]] || { echo "备份校验失败：$file" >&2; return 1; }
  done < "$snapshot/SHA256SUMS"
}
restore_snapshot() (
  set -e
  local snapshot="$1" volume logical archive file ref
  validate_snapshot "$snapshot"
  # Verify images before removing or restoring any application data.
  while IFS=$'\t' read -r service ref; do
    docker image inspect "$ref" >/dev/null 2>&1 || docker pull "$ref"
  done < "$snapshot/images.tsv"
  "${compose[@]}" stop
  while IFS=$'\t' read -r volume logical archive; do
    [[ "$volume" =~ ^[a-zA-Z0-9_.-]+$ && "$logical" =~ ^[a-zA-Z0-9_.-]+$ && "$archive" =~ ^volume-[0-9]+\.tar\.gz$ ]]
    [[ -z "$(docker ps -q --filter "volume=$volume")" ]] || { echo "数据卷仍被运行中的容器使用：$volume" >&2; exit 1; }
    docker volume inspect "$volume" >/dev/null 2>&1 || docker volume create --label "com.docker.compose.project=$project" --label "com.docker.compose.volume=$logical" "$volume" >/dev/null
    owned_volume "$volume" || { echo "数据卷项目不匹配或被其他项目使用：$volume" >&2; exit 1; }
    docker run --rm --pull never --network none --entrypoint sh \
      --mount "type=volume,source=$volume,target=/data" --mount "type=bind,source=$snapshot,target=/backup,readonly" \
      "$(cat "$snapshot/helper.image")" -ec 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzpf "/backup/$1" -C /data' sh "$archive"
  done < "$snapshot/volumes.tsv"
  while IFS= read -r file; do
    [[ "$file" =~ ^[a-zA-Z0-9_./-]+$ && "/$file/" != *'/../'* && "$file" != /* ]]
    rm -f -- "$ROOT/$file"
  done < "$snapshot/files.absent"
  tar -xzpf "$snapshot/configuration.tar.gz" -C "$ROOT"
  pending_file=$(mktemp "${ENV_FILE}.restore.XXXXXX")
  cp "$snapshot/environment.env" "$pending_file"
  chmod 600 "$pending_file"
  mv "$pending_file" "$ENV_FILE"
  resume_snapshot "$snapshot"
)

make_snapshot() {
  local directory file id service image volume logical destination writable alias count=0 mount_count=0 bytes=0 available size checksum archive
  mkdir -p "$backup_base"
  directory=$(mktemp -d "$backup_base/$(date +%Y%m%d-%H%M%S)-v${current}.XXXXXX")
  backup="$directory"
  printf '%s\n' 1 > "$directory/format"
  printf '%s\n' "$project" > "$directory/project.name"
  printf '%s\n' "$ROOT" > "$directory/root.path"
  printf '%s\n' "$helper" > "$directory/helper.image"
  cp "$ENV_FILE" "$directory/environment.env"
  : > "$directory/files.present"; : > "$directory/files.absent"
  while IFS= read -r file; do
    assert_managed_path "$ROOT" "$file" || return 1
    if [[ -f "$ROOT/$file" ]]; then echo "$file" >> "$directory/files.present"; else echo "$file" >> "$directory/files.absent"; fi
  done < "$managed_manifest"
  tar -czpf "$directory/configuration.tar.gz" -C "$ROOT" -T "$directory/files.present"
  : > "$directory/images.tsv"; : > "$directory/running.services"; : > "$directory/volumes.tsv"; : > "$directory/volume-names"
  echo 'services:' > "$directory/images.yaml"
  echo 'volumes:' > "$directory/volume-overrides.yaml"
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
    image=$(docker inspect --format '{{.Image}}' "$id")
    image=$(pin_image "$image")
    [[ "$service" =~ ^[a-zA-Z0-9_-]+$ && "$image" =~ ^[a-zA-Z0-9_./:@-]+$ ]] || return 1
    printf '%s\t%s\n' "$service" "$image" >> "$directory/images.tsv"
    printf '  %s:\n    image: "%s"\n' "$service" "$image" >> "$directory/images.yaml"
    if [[ "$(docker inspect --format '{{.State.Running}}' "$id")" == true ]]; then echo "$service" >> "$directory/running.services"; fi
    docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{printf "%s\t%s\t%t\n" .Name .Destination .RW}}{{end}}{{end}}' "$id" | sed '/^$/d' > "$directory/service-mounts"
    if [[ -s "$directory/service-mounts" ]]; then
      echo '    volumes:' >> "$directory/images.yaml"
      while IFS=$'\t' read -r volume destination writable; do
        [[ "$volume" =~ ^[a-zA-Z0-9_.-]+$ && "$destination" =~ ^/[a-zA-Z0-9_./-]+$ && "$writable" =~ ^(true|false)$ ]] || return 1
        mount_count=$((mount_count+1)); alias="nefex_snapshot_volume_$mount_count"
        printf '      - type: volume\n        source: %s\n        target: %s\n        read_only: %s\n' "$alias" "$destination" "$(if [[ "$writable" == true ]]; then echo false; else echo true; fi)" >> "$directory/images.yaml"
        printf '  %s:\n    name: %s\n    external: true\n' "$alias" "$volume" >> "$directory/volume-overrides.yaml"
        echo "$volume" >> "$directory/volume-names"
      done < "$directory/service-mounts"
    fi
  done < <("${compose[@]}" ps -aq)
  cat "$directory/volume-overrides.yaml" >> "$directory/images.yaml"
  sort -u "$directory/volume-names" > "$directory/unique-volumes"
  while IFS= read -r volume; do
    [[ -n "$volume" ]] || continue
    [[ "$volume" =~ ^[a-zA-Z0-9_.-]+$ ]] || return 1
    logical=$(docker volume inspect --format '{{index .Labels "com.docker.compose.volume"}}' "$volume")
    owned_volume "$volume" || { echo "不支持共享/外部数据卷：$volume" >&2; return 1; }
    if [[ "$logical" == '<no value>' || -z "$logical" ]]; then logical=anonymous; fi
    [[ "$logical" =~ ^[a-zA-Z0-9_.-]+$ ]] || return 1
    count=$((count+1))
    printf '%s\t%s\tvolume-%s.tar.gz\n' "$volume" "$logical" "$count" >> "$directory/volumes.tsv"
    size=$(docker run --rm --pull never --network none --entrypoint du --mount "type=volume,source=$volume,target=/data,readonly" "$helper" -sk /data | awk '{print $1}')
    [[ "$size" =~ ^[0-9]+$ ]] || return 1
    bytes=$((bytes+size))
  done < "$directory/unique-volumes"
  (( count >= 5 )) || { echo '未找到完整的 Nefex 数据卷。' >&2; return 1; }
  for logical in postgres minio redis searxng open-webui; do
    awk -F '\t' -v role="$logical" '$2 == role {found=1} END {exit !found}' "$directory/volumes.tsv" || {
      echo "缺少数据卷：$logical" >&2; return 1;
    }
  done
  available=$(df -Pk "$backup_base" | awk 'NR==2 {print $4}')
  (( available > bytes + bytes/10 + 102400 )) || { echo '备份目录磁盘空间不足。' >&2; return 1; }
  "${compose[@]}" -f "$directory/images.yaml" config > "$directory/compose.yaml"
  recovery="$directory"; transaction=stopped
  "${compose[@]}" stop
  while IFS=$'\t' read -r volume logical archive; do
    [[ -z "$(docker ps -q --filter "volume=$volume")" ]] || { echo "数据卷仍被运行中的容器使用，无法创建一致备份：$volume" >&2; return 1; }
    docker run --rm --pull never --network none --entrypoint tar \
      --mount "type=volume,source=$volume,target=/data,readonly" --mount "type=bind,source=$directory,target=/backup" \
      "$helper" -czpf "/backup/$archive" -C /data .
  done < "$directory/volumes.tsv"
  : > "$directory/SHA256SUMS"
  for file in format project.name root.path helper.image environment.env files.present files.absent configuration.tar.gz images.tsv running.services volumes.tsv compose.yaml 'volume-*.tar.gz'; do
    if [[ "$file" == 'volume-*.tar.gz' ]]; then
      for archive in "$directory"/volume-*.tar.gz; do
        checksum=$(nefex_hash "$archive"); [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] || return 1
        printf '%s  %s\n' "$checksum" "${archive##*/}" >> "$directory/SHA256SUMS"
      done
    else
      checksum=$(nefex_hash "$directory/$file"); [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] || return 1
      printf '%s  %s\n' "$checksum" "$file" >> "$directory/SHA256SUMS"
    fi
  done
  validate_snapshot "$directory" false
  touch "$directory/complete"
  echo "完整备份：$directory"
}

if [[ "$action" == rollback ]]; then
  [[ "$restore_path" == /* ]] || restore_path="$PWD/$restore_path"
  validate_snapshot "$restore_path"
  echo "将恢复备份的配置、数据和镜像：$restore_path"
  echo '恢复前也会为当前状态创建备份。'
  confirm
  make_snapshot
  recovery="$backup"; transaction=applied
  restore_snapshot "$restore_path"
  transaction=idle
  echo '回滚完成。'
  exit 0
fi
if [[ "$action" == backup ]]; then
  echo "将暂停项目 ${project}，创建一致的数据卷与配置快照，完成后恢复原运行状态。"
  confirm
  make_snapshot
  resume_snapshot "$backup"
  transaction=idle
  echo '备份完成，原服务运行状态已恢复。'
  exit 0
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/nefex-update.XXXXXX")
if [[ "$local_distribution" == false ]]; then
  distribution=$(nefex_distribution "$target" "$stage")
else distribution="$DIST_ROOT"; fi
managed_manifest="$distribution/install-files.txt"
validate_file_list "$distribution" || { echo '安装包文件清单不完整。' >&2; exit 2; }
requested_target="$target"
target="v$(tr -d '\r\n' < "$distribution/VERSION")"
[[ "$requested_target" == latest || "$requested_target" == "$target" ]] || { echo '本地安装包版本与 --to 不一致。' >&2; exit 2; }
nefex_version "$target" || exit 2
# Refuse direct downgrades: schema/data rollback must use a matching snapshot.
IFS=. read -r old_major old_minor old_patch <<< "$current"
IFS=. read -r new_major new_minor new_patch <<< "${target#v}"
if (( 10#$new_major < 10#$old_major || (10#$new_major == 10#$old_major && 10#$new_minor < 10#$old_minor) || (10#$new_major == 10#$old_major && 10#$new_minor == 10#$old_minor && 10#$new_patch < 10#$old_patch) )); then
  echo '不支持直接降级数据库，请使用 --rollback 恢复对应快照。' >&2; exit 2
fi
if [[ "v$current" == "$target" && -f "$ROOT/VERSION" && "v$(tr -d '\r\n' < "$ROOT/VERSION")" == "$target" ]]; then echo "已是 ${target}，无需更新。"; exit 0; fi
echo "更新：v$current → ${target}；项目：${project}；原配置、密钥和数据卷将保留。"
echo "更新前将暂停服务并备份到：$backup_base"
confirm
cpu_image="ghcr.io/albert-li-sz/nefex-web-ui:$target"
docker pull "$cpu_image"
cpu_image=$(pin_image "$cpu_image")
new_image="$cpu_image"
if [[ "$gpu" == true ]]; then
  new_image="ghcr.io/albert-li-sz/nefex-web-ui:${target}-cuda"
  docker pull "$new_image"
  new_image=$(pin_image "$new_image")
fi
reported=$(docker run --rm --pull never --network none --entrypoint python "$cpu_image" -c 'import json;print(json.load(open("/app/package.json"))["version"])')
[[ "v$reported" == "$target" ]] || { echo '镜像版本与发布包不匹配。' >&2; exit 1; }
CONFIG_FILE=$(mktemp "${ENV_FILE}.update.XXXXXX")
cp "$ENV_FILE" "$CONFIG_FILE"
write_env WEBUI_IMAGE "$cpu_image"
write_env WEBUI_GPU_IMAGE "$(if [[ "$gpu" == true ]]; then echo "$new_image"; else echo "ghcr.io/albert-li-sz/nefex-web-ui:${target}-cuda"; fi)"
export WEBUI_IMAGE="$cpu_image"
WEBUI_GPU_IMAGE=$(if [[ "$gpu" == true ]]; then echo "$new_image"; else echo "ghcr.io/albert-li-sz/nefex-web-ui:${target}-cuda"; fi)
export WEBUI_GPU_IMAGE
write_env COMPOSE_PROJECT_NAME "$project"
write_env NEFEX_INSTALL_GPU "$gpu"
# Infrastructure updates are explicit; application updates retain the exact running images.
for mapping in 'postgres POSTGRES_IMAGE' 'minio MINIO_IMAGE' 'redis REDIS_IMAGE' 'searxng SEARXNG_IMAGE'; do
  read -r service key <<< "$mapping"
  id=$("${compose[@]}" ps -aq "$service")
  image=$(docker inspect --format '{{.Image}}' "$id")
  pinned=$(pin_image "$image")
  write_env "$key" "$pinned"
  export "$key=$pinned"
done
new_compose=(docker compose --project-directory "$ROOT" --env-file "$CONFIG_FILE" -p "$project" -f "$distribution/docker-compose.yaml")
if [[ "$gpu" == true ]]; then new_compose+=(-f "$distribution/docker-compose.gpu.yaml"); fi
WEBUI_ENV_FILE="$CONFIG_FILE" "${new_compose[@]}" config --quiet
make_snapshot
transaction=applied
# Atomic renames keep a running maintenance script's old inode intact.
while IFS= read -r file; do
  assert_managed_path "$ROOT" "$file"
  mkdir -p "$(dirname "$ROOT/$file")"
  pending_file=$(mktemp "$ROOT/${file}.new.XXXXXX")
  cp -p "$distribution/$file" "$pending_file"
  mv "$pending_file" "$ROOT/$file"
  pending_file=''
done < "$distribution/install-files.txt"
mv "$CONFIG_FILE" "$ENV_FILE"; CONFIG_FILE=''
chmod 600 "$ENV_FILE"
new_compose=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -p "$project" -f "$ROOT/docker-compose.yaml")
if [[ "$gpu" == true ]]; then new_compose+=(-f "$ROOT/docker-compose.gpu.yaml"); fi
"${new_compose[@]}" up -d --no-build --pull never --wait --wait-timeout "$wait_timeout"
"${new_compose[@]}" exec -T open-webui python check_stack.py
transaction=idle
"${new_compose[@]}" ps -a
echo "更新完成：${target}；保留的更新前快照：$backup"
