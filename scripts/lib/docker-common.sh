#!/usr/bin/env bash
# Shared dotenv and single-writer lock helpers. Never source user configuration.
# Read data, never source/eval a dotenv file. Decode values written by this installer.
read_env() {
  local raw
  raw=$(awk -v key="$1" '
    {
      line=$0; sub(/^[ \t]*(export[ \t]+)?/, "", line)
      at=index(line, "="); if (!at) next
      name=substr(line, 1, at-1); sub(/[ \t]+$/, "", name)
      if (name != key) next
      value=substr(line, at+1); sub(/^[ \t]+/, "", value)
      quote=substr(value,1,1)
      if (quote == "\"" || quote == sprintf("%c",39)) {
        escaped=0
        for (i=2; i<=length(value); i++) {
          c=substr(value,i,1)
          if (c == quote && !escaped) {value=substr(value,1,i); break}
          if (c == "\\" && !escaped) escaped=1; else escaped=0
        }
      } else {sub(/[ \t]+#.*/, "", value); sub(/[ \t]+$/, "", value)}
      found=value
    }
    END {printf "%s", found}
  ' "${2:-$CONFIG_FILE}")
  case "$raw" in
    \"*\")
      raw=${raw#\"}; raw=${raw%\"}
      raw=${raw//\\\"/\"}; raw=${raw//\\\\/\\}; raw=${raw//\$\$/\$}
      ;;
    \'*\') raw=${raw#\'}; raw=${raw%\'}; raw=${raw//\\\'/\'} ;;
  esac
  printf '%s' "$raw"
}

write_env() {
  local key="$1" value="$2" escaped
  [[ "$(read_env "$key")" != "$value" ]] || return 0
  pending_file=$(mktemp "${CONFIG_FILE}.XXXXXX")
  awk -v key="$key" '{
    line=$0; sub(/^[ \t]*(export[ \t]+)?/, "", line); at=index(line,"=")
    name=substr(line,1,at-1); sub(/[ \t]+$/,"",name)
    if (!at || name != key) print
  }' "$CONFIG_FILE" > "$pending_file"
  case "$value" in
    *[!a-zA-Z0-9_./:@+-]*|'')
      escaped=${value//\\/\\\\}; escaped=${escaped//\"/\\\"}; escaped=${escaped//\$/\$\$}
      printf '%s="%s"\n' "$key" "$escaped" >> "$pending_file"
      ;;
    *) printf '%s=%s\n' "$key" "$value" >> "$pending_file" ;;
  esac
  mv "$pending_file" "$CONFIG_FILE"
  pending_file=''
}


lock_dir=''
acquire_lock() {
  local candidate="$1.nefex-lock" owner=''
  if ! mkdir "$candidate" 2>/dev/null; then
    if [[ -d "$candidate" && ! -L "$candidate" && -O "$candidate" && -f "$candidate/owner.pid" ]]; then
      owner=$(cat "$candidate/owner.pid")
      if [[ "$owner" =~ ^[0-9]+$ ]] && ! kill -0 "$owner" 2>/dev/null; then
        rm -f "$candidate/owner.pid"
        rmdir "$candidate" 2>/dev/null || true
      fi
    fi
    if ! mkdir "$candidate" 2>/dev/null; then
      echo "已有安装或维护进程正在运行：$candidate" >&2
      return 1
    fi
  fi
  lock_dir="$candidate"
  printf '%s\n' "$$" > "$lock_dir/owner.pid"
}
release_lock() {
  if [[ -n "$lock_dir" && -f "$lock_dir/owner.pid" && "$(cat "$lock_dir/owner.pid")" == "$$" ]]; then
    rm -f "$lock_dir/owner.pid"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}

assert_managed_path() {
  local file="$2" part path="$1"
  local parts=()
  [[ "$file" =~ ^[a-zA-Z0-9_./-]+$ && "/$file/" != *'/../'* && "$file" != /* ]] || return 1
  IFS=/ read -r -a parts <<< "$file"
  for part in "${parts[@]}"; do
    path="$path/$part"
    [[ ! -L "$path" ]] || { echo "自定义符号链接不支持自动维护：$file" >&2; return 1; }
  done
}
