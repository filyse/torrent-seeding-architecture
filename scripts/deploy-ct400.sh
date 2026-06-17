#!/usr/bin/env bash
# Канонический деплой стека на CT400.
#
# ВАЖНО: всегда поднимает стек с media-оверреем. Без него движки пересоздаются на
# пустых локальных томах и теряют контент/.torrent, лежащие на шаре /mnt/media
# (раздачи становятся неактивными). Используй ТОЛЬКО этот скрипт для деплоя.
#
# Примеры:
#   scripts/deploy-ct400.sh up -d --build      # пересборка и запуск
#   scripts/deploy-ct400.sh up -d              # запуск
#   scripts/deploy-ct400.sh restart api        # рестарт сервиса
#   scripts/deploy-ct400.sh ps                 # статус
# Без аргументов — эквивалент `up -d`.
set -euo pipefail

cd "$(dirname "$0")/.."

BASE="docker-compose.multi-engine.yml"
MEDIA="docker-compose.multi-engine.media.yml"

for f in "$BASE" "$MEDIA"; do
  if [ ! -f "$f" ]; then
    echo "ОШИБКА: не найден $f в $(pwd). Деплой остановлен, чтобы не поднять движки без шары." >&2
    exit 1
  fi
done

if [ "$#" -eq 0 ]; then
  set -- up -d
fi

set -x
exec docker compose -f "$BASE" -f "$MEDIA" "$@"
