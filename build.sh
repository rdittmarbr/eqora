#!/usr/bin/env bash
set -euo pipefail

# Exit code 1: erro bash/comando
# Exit code 2: erro validação opcional python

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[1;31m'
YELLOW='\033[1;33m'
GREEN='\033[1;32m'
RESET='\033[0m'

log()  { echo -e "${GREEN}[build]${RESET} $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET} $*"; }
err()  { echo -e "${RED}[erro]${RESET} $*" >&2; }

show_help() {
  cat <<'USAGE'
Uso:
  SERVER_TYPE=<development|homologation|staging|production> ./build.sh [opcoes] <comando>

Comandos:
  all              Build completo + up (staging/prod executam builds JS)
  up               Sobe stack (ou --service)
  down             Derruba stack
  build            Build imagens (ou --service)
  restart          Reinicia stack (ou --service)
  logs             Logs da stack (ou --service)
  ps               Status dos containers
  config           Renderiza compose efetivo
  validate         Renderiza compose e valida pre-requisitos
  install          Instala/configura Laravel no container da API
  migration        Executa php artisan migrate (--seed opcional)
  run              Executa builds JS (web_build + admin_build + web_mobile_build)
  mobile           Executa build mobile (web_mobile_build)

Opcoes:
  -e, --env-file <arquivo>   Usa este .env em vez de .env.<ambiente>
  -f <arquivo>               Compose custom (pode repetir). Se usar -f, substitui o conjunto padrao
  --service <nome>           Atua apenas sobre um servico
  --with-deps                Com --service no up, sobe com dependencias
  --seed                     Com migration, executa seed
  --dry-run                  Nao executa acoes destrutivas
  -h, --help                 Mostra ajuda
USAGE
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Comando obrigatorio nao encontrado: $1"; exit 1; }
}

# Descobre CLI do compose
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DOCKER=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER=(docker-compose)
else
  err "Docker Compose nao encontrado (docker compose ou docker-compose)."
  exit 1
fi

# Ambiente com fallback
SERVER_TYPE_RAW="${SERVER_TYPE:-development}"
case "$SERVER_TYPE_RAW" in
  development|homologation|staging|production)
    ENVIRONMENT="$SERVER_TYPE_RAW"
    ;;
  *)
    warn "SERVER_TYPE invalido ('$SERVER_TYPE_RAW'). Usando development."
    ENVIRONMENT="development"
    ;;
esac

# Defaults
ENV_FILE=".env.${ENVIRONMENT}"
USER_ENV_FILE=false
DRY_RUN=false
WITH_DEPS=false
SEED_FLAG=false
SERVICE_FILTER=""
APP_SERVICE="${APP_SERVICE:-api}"
DB_SERVICE="${DB_SERVICE:-db}"
APP_WORKDIR="${APP_WORKDIR:-/var/www/html}"

USER_COMPOSE_FILES=()
ACTION=""

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    -e|--env-file)
      [[ $# -ge 2 ]] || { err "Falta arquivo apos $1"; exit 1; }
      ENV_FILE="$2"
      USER_ENV_FILE=true
      shift 2
      ;;
    -f)
      [[ $# -ge 2 ]] || { err "Falta arquivo apos -f"; exit 1; }
      USER_COMPOSE_FILES+=("$2")
      shift 2
      ;;
    --service)
      [[ $# -ge 2 ]] || { err "Falta nome apos --service"; exit 1; }
      SERVICE_FILTER="$2"
      shift 2
      ;;
    --with-deps)
      WITH_DEPS=true
      shift
      ;;
    --seed)
      SEED_FLAG=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      if [[ -z "$ACTION" ]]; then
        ACTION="$1"
      else
        err "Argumento inesperado: $1"
        exit 1
      fi
      shift
      ;;
  esac
done

ACTION="${ACTION:-all}"

[[ -f "$ENV_FILE" ]] || { err "Arquivo de ambiente nao encontrado: $ENV_FILE"; exit 1; }

# Carrega env para validacoes locais
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# coerencia opcional
if [[ -n "${ENV_TYPE:-}" ]]; then
  case "${ENV_TYPE}" in
    development|homologation|staging|production) ;;
    dev) ENV_TYPE="development" ;;
    homolog) ENV_TYPE="homologation" ;;
    *) warn "ENV_TYPE no env-file invalido (${ENV_TYPE})." ;;
  esac
  if [[ "${ENV_TYPE}" != "${ENVIRONMENT}" ]]; then
    err "ENV_TYPE (${ENV_TYPE}) diferente de SERVER_TYPE resolvido (${ENVIRONMENT})."
    exit 1
  fi
fi

PROJECT_BASE="${COMPOSE_PROJECT_NAME:-$(basename "$SCRIPT_DIR")}"
PROJECT_NAME="${PROJECT_NAME:-${PROJECT_BASE}-${ENVIRONMENT}}"

COMPOSE_DEFAULT=(
  docker-compose.yaml
  docker-compose.nginx.yaml
  docker-compose.api.yaml
  docker-compose.web.yaml
  docker-compose.db.yaml
  docker-compose.mobile.yaml
  "docker-compose.${ENVIRONMENT}.yaml"
)

COMPOSE_ARGS=()
if ((${#USER_COMPOSE_FILES[@]} > 0)); then
  for f in "${USER_COMPOSE_FILES[@]}"; do
    [[ -f "$f" ]] || { err "Compose nao encontrado: $f"; exit 1; }
    COMPOSE_ARGS+=( -f "$f" )
  done
else
  for f in "${COMPOSE_DEFAULT[@]}"; do
    [[ -f "$f" ]] || { err "Compose nao encontrado: $f"; exit 1; }
    COMPOSE_ARGS+=( -f "$f" )
  done
fi

COMPOSE_TEST=".docker/docker-compose-${ENVIRONMENT}.yaml"
mkdir -p .docker

dc() {
  "${DOCKER[@]}" --env-file "$ENV_FILE" -p "$PROJECT_NAME" "${COMPOSE_ARGS[@]}" "$@"
}

service_exists() {
  local s="$1"
  dc config --services | grep -Fxq "$s"
}

service_exists_build_profile() {
  local s="$1"
  dc --profile build config --services | grep -Fxq "$s"
}

render_effective_compose() {
  dc config > "$COMPOSE_TEST"
}

create_bind_dirs() {
  if command -v jq >/dev/null 2>&1; then
    dc config --format json \
      | jq -r '.services | to_entries[] | .value.volumes? // [] | map(select(.type=="bind") | .source) | .[]' \
      | while IFS= read -r src; do
          [[ -n "$src" ]] || continue
          case "$src" in
            /var/*|/etc/*|/usr/*) continue ;;
          esac
          if [[ -e "$src" ]]; then
            continue
          fi

          # Se parecer arquivo de bind (ex: *.conf), cria apenas o diretório pai.
          if [[ "$src" == */ ]]; then
            mkdir -p "$src"
          elif [[ "$(basename "$src")" == *.* ]]; then
            mkdir -p "$(dirname "$src")"
          else
            mkdir -p "$src"
          fi
        done
  else
    warn "jq nao encontrado; pulando criacao automatica de binds."
  fi
}

run_js_builds() {
  log "Executando builds JS (web_build + admin_build + web_mobile_build)..."
  if service_exists_build_profile web_build; then
    dc --profile build run --rm web_build
  else
    warn "Servico web_build nao existe neste ambiente; pulando."
  fi

  if service_exists_build_profile admin_build; then
    dc --profile build run --rm admin_build
  else
    warn "Servico admin_build nao existe neste ambiente; pulando."
  fi

  if service_exists_build_profile web_mobile_build; then
    dc --profile build run --rm web_mobile_build
  else
    warn "Servico web_mobile_build nao existe neste ambiente; pulando."
  fi
}

run_mobile_build() {
  log "Executando build mobile (web_mobile_build)..."
  dc --profile build run --rm web_mobile_build
}

run_install() {
  service_exists "$APP_SERVICE" || { err "Servico APP nao existe: $APP_SERVICE"; exit 1; }
  service_exists "$DB_SERVICE" || { err "Servico DB nao existe: $DB_SERVICE"; exit 1; }

  $DRY_RUN && { log "[dry-run] install"; return 0; }

  log "Subindo ${APP_SERVICE} e ${DB_SERVICE}..."
  dc up -d "$APP_SERVICE" "$DB_SERVICE"

  local force_flag=""
  if [[ "$ENVIRONMENT" != "development" ]]; then
    force_flag="--force"
  fi

  log "Executando instalacao Laravel em ${APP_SERVICE} (${APP_WORKDIR})..."
  dc exec -T -w "$APP_WORKDIR" "$APP_SERVICE" sh -lc "
    set -e
    if [ -f composer.json ]; then
      composer install --no-interaction
    else
      composer create-project --no-interaction --prefer-dist laravel/laravel:^12.0 .
    fi

    if [ -f artisan ]; then
      if [ ! -f .env ] && [ -f .env.example ]; then
        cp .env.example .env
      fi
      php artisan key:generate || true
      php artisan migrate ${force_flag}
      php artisan --version || true
    else
      echo 'artisan nao encontrado.'
    fi
  "
}

run_migration() {
  service_exists "$APP_SERVICE" || { err "Servico APP nao existe: $APP_SERVICE"; exit 1; }
  $DRY_RUN && { log "[dry-run] migration"; return 0; }

  local flags=(--force)
  $SEED_FLAG && flags+=(--seed)

  log "Subindo ${APP_SERVICE}..."
  dc up -d "$APP_SERVICE"

  log "Executando migrations..."
  dc exec -T -w "$APP_WORKDIR" "$APP_SERVICE" php artisan migrate "${flags[@]}"
}

validate_optional_python() {
  if [[ -f "build_validate.py" ]]; then
    require_cmd python3
    if ! python3 -c "import yaml" >/dev/null 2>&1; then
      err "PyYAML nao encontrado para build_validate.py"
      exit 2
    fi
    log "Executando build_validate.py (opcional)"
    python3 ./build_validate.py -f "$COMPOSE_TEST" --env-type "$ENVIRONMENT" --env-file "$ENV_FILE" || exit 2
  fi
}

show_context() {
  log "Ambiente: $ENVIRONMENT"
  log "Env file: $ENV_FILE"
  log "Projeto : $PROJECT_NAME"
  log "Compose :"
  local i
  for ((i=0; i<${#COMPOSE_ARGS[@]}; i+=2)); do
    echo "  - ${COMPOSE_ARGS[i+1]}"
  done
  if [[ -n "$SERVICE_FILTER" ]]; then
    log "Servico alvo: $SERVICE_FILTER"
  fi
}

require_cmd "${DOCKER[0]}"
show_context

case "$ACTION" in
  config)
    render_effective_compose
    create_bind_dirs
    log "Compose efetivo salvo em $COMPOSE_TEST"
    ;;
  validate)
    render_effective_compose
    create_bind_dirs
    validate_optional_python
    log "Validacao concluida"
    ;;
  build)
    render_effective_compose
    create_bind_dirs
    $DRY_RUN && { log "[dry-run] build"; exit 0; }
    if [[ -n "$SERVICE_FILTER" ]]; then
      service_exists "$SERVICE_FILTER" || { err "Servico nao existe: $SERVICE_FILTER"; exit 1; }
      dc build "$SERVICE_FILTER"
    else
      dc build
    fi
    ;;
  up)
    render_effective_compose
    create_bind_dirs
    $DRY_RUN && { log "[dry-run] up"; exit 0; }
    if [[ -n "$SERVICE_FILTER" ]]; then
      service_exists "$SERVICE_FILTER" || { err "Servico nao existe: $SERVICE_FILTER"; exit 1; }
      if $WITH_DEPS; then
        dc up -d --build "$SERVICE_FILTER"
      else
        dc up -d --build --no-deps "$SERVICE_FILTER"
      fi
    else
      dc up -d
    fi
    ;;
  down)
    $DRY_RUN && { log "[dry-run] down"; exit 0; }
    dc down --remove-orphans
    ;;
  restart)
    $DRY_RUN && { log "[dry-run] restart"; exit 0; }
    if [[ -n "$SERVICE_FILTER" ]]; then
      service_exists "$SERVICE_FILTER" || { err "Servico nao existe: $SERVICE_FILTER"; exit 1; }
      dc restart "$SERVICE_FILTER"
    else
      dc restart
    fi
    ;;
  logs)
    if [[ -n "$SERVICE_FILTER" ]]; then
      dc logs -f --tail=200 "$SERVICE_FILTER"
    else
      dc logs -f --tail=200
    fi
    ;;
  ps)
    dc ps
    ;;
  install)
    render_effective_compose
    create_bind_dirs
    run_install
    ;;
  migration)
    render_effective_compose
    create_bind_dirs
    run_migration
    ;;
  run)
    $DRY_RUN && { log "[dry-run] run"; exit 0; }
    run_js_builds
    ;;
  mobile)
    $DRY_RUN && { log "[dry-run] mobile"; exit 0; }
    run_mobile_build
    ;;
  all)
    render_effective_compose
    create_bind_dirs
    $DRY_RUN && { log "[dry-run] all"; exit 0; }
    dc build
    if [[ "$ENVIRONMENT" == "production" ]]; then
      run_js_builds
    fi
    dc up -d
    ;;
  *)
    err "Comando invalido: $ACTION"
    show_help
    exit 1
    ;;
esac
