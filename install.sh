#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log() {
  printf '[install] %s\n' "$*"
}

warn() {
  printf '[warn] %s\n' "$*" >&2
}

err() {
  printf '[erro] %s\n' "$*" >&2
}

clone_repo() {
  local repo_url="$1"
  local dest_dir="$2"

  if [[ -d "$dest_dir" ]] && [[ -n "$(ls -A "$dest_dir" 2>/dev/null)" ]]; then
    warn "Pasta '$dest_dir' ja existe e nao esta vazia. Pulando clone."
    return 0
  fi

  if [[ -d "$dest_dir" ]]; then
    rmdir "$dest_dir" 2>/dev/null || true
  fi

  log "Clonando $repo_url -> $dest_dir"
  git clone "$repo_url" "$dest_dir"
}

require_repo_url() {
  local value="$1"
  local env_name="$2"
  local label="$3"

  if [[ -z "$value" ]]; then
    err "URL do repositorio nao definida para '$label'."
    err "Defina a variavel de ambiente $env_name e execute novamente."
    return 1
  fi
}

# URLs conhecidas nesta maquina (inferidas dos remotes existentes)
APP_API_REPO_URL="${APP_API_REPO_URL:-git@github.com:rdittmarbr/Eqora_api.git}"
APP_CLIENT_REPO_URL="${APP_CLIENT_REPO_URL:-git@github.com:rdittmarbr/EQora_app.git}"

# Preencher conforme seus repositorios privados
APP_ADMIN_REPO_URL="${APP_ADMIN_REPO_URL:-}"
APP_WEB_MOBILE_REPO_URL="${APP_WEB_MOBILE_REPO_URL:-}"

require_repo_url "$APP_API_REPO_URL" "APP_API_REPO_URL" "app_api"
require_repo_url "$APP_CLIENT_REPO_URL" "APP_CLIENT_REPO_URL" "app_client"
require_repo_url "$APP_ADMIN_REPO_URL" "APP_ADMIN_REPO_URL" "app_admin"
require_repo_url "$APP_WEB_MOBILE_REPO_URL" "APP_WEB_MOBILE_REPO_URL" "app-mobile"

clone_repo "$APP_API_REPO_URL" "app_api"
clone_repo "$APP_CLIENT_REPO_URL" "app_client"
clone_repo "$APP_ADMIN_REPO_URL" "app_admin"
clone_repo "$APP_WEB_MOBILE_REPO_URL" "app-mobile"

log "Concluido."
