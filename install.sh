#!/usr/bin/env bash
# Standalone release bootstrap; also supplies verified download helpers to maintenance.
set -euo pipefail

nefex_hash() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}';
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
nefex_version() { [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
nefex_fetch() {
  curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error \
    --retry 2 --connect-timeout 15 --max-time 180 "$1" -o "$2"
}
nefex_distribution() {
  local version="$1" destination="$2" url archive expected actual entry relative prefix
  command -v curl >/dev/null || { echo '需要 curl 下载发布包。' >&2; return 2; }
  if [[ "$version" == latest ]]; then
    url=$(curl --proto '=https' --proto-redir '=https' --fail --location --head --silent --show-error \
      --retry 2 --connect-timeout 15 --max-time 60 --output /dev/null --write-out '%{url_effective}' \
      'https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/latest')
    version=${url##*/}
  fi
  nefex_version "$version" || { echo '版本必须是 v主版本.次版本.补丁版本。' >&2; return 2; }
  archive="nefex-web-ui-${version}-docker.tar.gz"
  url="https://github.com/Albert-Li-Sz/Nefex-Web-UI/releases/download/$version"
  echo "下载并校验 $version 安装包……" >&2
  nefex_fetch "$url/SHA256SUMS" "$destination/SHA256SUMS" || return $?
  nefex_fetch "$url/$archive" "$destination/$archive" || return $?
  expected=$(awk -v name="$archive" '$2 == name {print $1}' "$destination/SHA256SUMS")
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo '发布包缺少唯一 SHA256 校验值。' >&2; return 1; }
  actual=$(nefex_hash "$destination/$archive")
  [[ "$actual" == "$expected" ]] || { echo '安装包 SHA256 不匹配，已中止。' >&2; return 1; }
  prefix="nefex-web-ui-${version}-docker"
  tar -tzf "$destination/$archive" > "$destination/archive-files" || return $?
  while IFS= read -r entry; do
    [[ "$entry" == "$prefix/"* ]] || { echo '安装包目录异常。' >&2; return 1; }
    relative=${entry#"$prefix/"}
    [[ "$relative" =~ ^[a-zA-Z0-9_./-]*$ && "/$relative/" != *'/../'* && "$relative" != /* ]] || return 1
  done < "$destination/archive-files"
  tar -tvzf "$destination/$archive" | awk 'substr($0,1,1)!="-" && substr($0,1,1)!="d" {bad=1} END {exit bad}' || {
    echo '安装包不允许包含链接或特殊文件。' >&2; return 1;
  }
  tar -xzf "$destination/$archive" -C "$destination" || return $?
  [[ -f "$destination/$prefix/install-files.txt" ]] || { echo '目标版本缺少安装清单。' >&2; return 1; }
  [[ -f "$destination/$prefix/VERSION" && "$(tr -d '\r\n' < "$destination/$prefix/VERSION")" == "${version#v}" ]] || {
    echo '发布包版本与目标标签不匹配。' >&2; return 1;
  }
  printf '%s\n' "$destination/$prefix"
}

nefex_bootstrap() {
  local directory='' version=latest action='' stage distribution status=0
  local forwarded=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --install-dir|--to|--version)
        [[ $# -ge 2 ]] || { echo "Missing $1 value" >&2; return 2; }
        if [[ "$1" == --install-dir ]]; then directory="$2"; else version="$2"; fi
        shift ;;
      --update|--backup|--list-backups|--check) action="$1" ;;
      --rollback)
        [[ $# -ge 2 ]] || return 2
        action=--rollback; forwarded+=("$1" "$2"); shift ;;
      -h|--help)
        echo 'Usage: bash install.sh [--install-dir PATH] [--update] [--to v0.12.9] [installer options]'
        echo '已有安装默认更新；首次安装进入配置向导。--backup、--rollback PATH、--non-interactive 也可用。'
        return 0 ;;
      *) forwarded+=("$1") ;;
    esac
    shift
  done
  if [[ -z "$directory" ]]; then
    if [[ -f .env && -f docker-compose.yaml ]]; then directory="$PWD"; else directory="$PWD/nefex-web-ui"; fi
  fi
  [[ "$directory" == /* ]] || directory="$PWD/$directory"
  if [[ -z "$action" && -f "$directory/.env" ]]; then action=--update; fi
  stage=$(mktemp -d "${TMPDIR:-/tmp}/nefex-download.XXXXXX")
  distribution=$(nefex_distribution "$version" "$stage") || { status=$?; rm -rf -- "$stage"; return "$status"; }
  if [[ -n "$action" ]]; then
    if [[ "$action" == --rollback ]]; then
      bash "$distribution/scripts/install-docker.sh" --install-dir "$directory" ${forwarded[@]+"${forwarded[@]}"} || status=$?
    else
      bash "$distribution/scripts/install-docker.sh" "$action" --local --install-dir "$directory" ${forwarded[@]+"${forwarded[@]}"} || status=$?
    fi
  else
    if [[ -d "$directory" && -n "$(ls -A "$directory")" ]]; then
      echo '安装目录非空；已有安装请使用 --update，首次安装请选择空目录。' >&2
      status=2
    else
      mkdir -p "$directory"
      cp -R "$distribution/." "$directory/"
      bash "$directory/scripts/install-docker.sh" ${forwarded[@]+"${forwarded[@]}"} || status=$?
    fi
  fi
  rm -rf -- "$stage"
  return "$status"
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then nefex_bootstrap "$@"; fi
