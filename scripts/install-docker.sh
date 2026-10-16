#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT/.env"
init_only=false
build=false
pull=true
check_only=false
gpu=false

usage() {
  cat <<'EOF'
Usage: ./scripts/install-docker.sh [OPTIONS]
  --init-only        Create configuration and secrets without starting services
  --env-file PATH    Use a separate environment file
  --build            Build the application and patched MinIO from source
  --no-build         Use cached images without pulling or building
  --gpu              Enable NVIDIA CUDA for local embedding and speech models
  --check            Check a running installation, including real search results
  -h, --help         Show help

The default command pulls published images and starts the complete stack, waits for healthy
services, and checks database, vector search, storage, Redis and web search.
Existing configuration and data volumes are retained on subsequent runs.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-only) init_only=true ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo 'Missing --env-file path' >&2; exit 2; }
      ENV_FILE="$2"; shift ;;
    --build) build=true; pull=false ;;
    --no-build) build=false; pull=false ;;
    --gpu) gpu=true ;;
    --check) check_only=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [[ "$ENV_FILE" != /* ]]; then
  ENV_FILE="$PWD/$ENV_FILE"
fi

command -v docker >/dev/null || { echo 'Install Docker Engine/Desktop with Compose v2 first.' >&2; exit 1; }
docker compose version >/dev/null

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
fi

# Never source user configuration as shell code or replace an existing secret.
ensure_secret() {
  local key="$1" value tmp
  value=$(awk -v key="$key" 'index($0, key "=") == 1 {print substr($0, length(key) + 2); exit}' "$ENV_FILE")
  if [[ -z "$value" || "$value" == "''" || "$value" == '""' ]]; then
    value=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    tmp=$(mktemp "${ENV_FILE}.XXXXXX")
    awk -v key="$key" 'index($0, key "=") != 1 {print}' "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
  fi
}

for key in POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_PASSWORD WEBUI_SECRET_KEY SEARXNG_SECRET; do
  ensure_secret "$key"
done
chmod 600 "$ENV_FILE"

export WEBUI_ENV_FILE="$ENV_FILE"
compose=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -f "$ROOT/docker-compose.yaml")
if [[ "$gpu" == true ]]; then
  compose+=(-f "$ROOT/docker-compose.gpu.yaml")
fi
"${compose[@]}" config --quiet

if [[ "$init_only" == true ]]; then
  echo "Configuration ready: $ENV_FILE"
  echo 'Set your model API URL/key in this file, then rerun the installer.'
  exit 0
fi

docker info >/dev/null
if [[ "$check_only" != true ]]; then
  if [[ "$build" == true ]]; then
    # The complete application and the MinIO compiler both need memory.
    # Build them sequentially; minio-init reuses the application image.
    "${compose[@]}" pull postgres redis searxng
    if [[ "$gpu" == true ]]; then
      "${compose[@]}" pull minio-init
    fi
    "${compose[@]}" build minio
    "${compose[@]}" build open-webui
  elif [[ "$pull" == true ]]; then
    "${compose[@]}" pull
  fi
  if ! "${compose[@]}" up -d --no-build --wait --wait-timeout 600; then
    "${compose[@]}" ps -a
    "${compose[@]}" logs --tail 60
    exit 1
  fi
fi

"${compose[@]}" exec -T open-webui python check_stack.py
"${compose[@]}" ps -a
echo 'Installation checks passed. Open the published WebUI port (default http://localhost:3000).'
