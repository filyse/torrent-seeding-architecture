#!/usr/bin/env bash
# Развёртка автономного движка на отдельной машине (Фаза 4.5).
#
# Использование:
#   cp .env.engine.example .env.engine   # один раз, затем заполнить значения
#   ./scripts/deploy-engine.sh           # из корня репозитория
#
# Переменные читаются из .env.engine (или из файла в ENV_FILE=...).
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env.engine}"
COMPOSE_FILE="docker-compose.engine.yml"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '\033[36m%s\033[0m\n' "$*"; }

# --- проверки окружения ---
if ! command -v docker >/dev/null 2>&1; then
  red "docker не найден. Установи Docker: https://docs.docker.com/engine/install/"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  red "'docker compose' недоступен (нужен Docker Compose v2)."
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  red "Нет файла $ENV_FILE."
  info "Создай и заполни его:  cp .env.engine.example $ENV_FILE && nano $ENV_FILE"
  exit 1
fi

# --- загрузка и валидация переменных ---
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

missing=()
for var in SEEDING_ENGINE_ID SEEDING_ORCHESTRATOR_URL SEEDING_ENGINE_REGISTER_KEY \
           SEEDING_ENGINE_ADVERTISE_URL SEEDING_ENGINE_LISTEN_PORT; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done
if (( ${#missing[@]} > 0 )); then
  red "Не заданы обязательные переменные в $ENV_FILE: ${missing[*]}"
  exit 1
fi

info "Движок:        $SEEDING_ENGINE_ID"
info "Оркестратор:   $SEEDING_ORCHESTRATOR_URL"
info "Advertise URL: $SEEDING_ENGINE_ADVERTISE_URL"
info "BT-порт:       $SEEDING_ENGINE_LISTEN_PORT (TCP+UDP)"
info "API-порт:      ${SEEDING_ENGINE_API_PORT:-8081}"

# Предупреждение про localhost в advertise (оркестратор не достучится).
case "$SEEDING_ENGINE_ADVERTISE_URL" in
  *127.0.0.1*|*localhost*)
    red "ВНИМАНИЕ: SEEDING_ENGINE_ADVERTISE_URL указывает на localhost — оркестратор не сможет"
    red "          подключиться к этому движку. Укажи внешний IP этой машины."
    ;;
esac

# --- проверка доступности оркестратора (необязательная, не блокирующая) ---
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -m 5 "${SEEDING_ORCHESTRATOR_URL%/}/api/v1/health" >/dev/null 2>&1; then
    green "Оркестратор доступен."
  else
    red "Не удалось достучаться до оркестратора по ${SEEDING_ORCHESTRATOR_URL%/}/api/v1/health."
    red "Проверь адрес/сеть/файрвол. Продолжаю — движок будет повторять регистрацию сам."
  fi
fi

# --- сборка и запуск ---
info "Сборка и запуск движка…"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

# --- ожидание health ---
cname="${SEEDING_ENGINE_ID}-seeding"
info "Жду готовности контейнера $cname…"
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$cname" 2>/dev/null || echo unknown)"
  if [[ "$status" == "healthy" ]]; then
    green "Движок здоров (healthy)."
    break
  fi
  sleep 3
done

echo
green "Готово. Движок '$SEEDING_ENGINE_ID' поднят и сам регистрируется в оркестраторе."
info  "Проверка регистрации на оркестраторе:"
info  "  curl ${SEEDING_ORCHESTRATOR_URL%/}/api/v1/engines/registry | grep $SEEDING_ENGINE_ID"
info  "Локальные логи движка:  docker logs -f $cname"
