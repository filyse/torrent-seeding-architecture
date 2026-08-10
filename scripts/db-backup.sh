#!/usr/bin/env bash
# Суточный бэкап: дамп Postgres (реестр раздач/движков) + состояние движков
# (.fastresume/.torrents/.state) на медиа-шару, с ретеншеном.
# Запуск на CT400 (внутри контейнера живёт docker). Кладём дампы на /mnt/media —
# это отдельный диск от rootfs, поэтому бэкап переживает пересоздание CT/тома pgdata.
#
# Cron (ежедневно в 04:30). Вызов ЧЕРЕЗ bash, а не напрямую: если файл потеряет бит
# запуска (например, после `git reset --hard` с чекаута, где режимы не сохранились),
# прямой вызов молча падает в «Permission denied» и бэкапы прекращаются незаметно.
#   30 4 * * * bash /opt/containerd/scripts/db-backup.sh >> /var/log/db-backup.log 2>&1
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-containerd-db-1}"
API_CONTAINER="${API_CONTAINER:-containerd-api-1}"
DB_USER="${POSTGRES_USER:-seeding}"
DB_NAME="${POSTGRES_DB:-seeding}"
BACKUP_DIR="${DB_BACKUP_DIR:-/mnt/media/seeding-test/_backups/db}"
RETENTION_DAYS="${DB_BACKUP_RETENTION_DAYS:-14}"

log() { echo "[$(date '+%F %T')] $*"; }

if ! docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
  log "ОШИБКА: контейнер БД '$DB_CONTAINER' не найден"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
OUT="$BACKUP_DIR/${DB_NAME}-${STAMP}.dump"
TMP="$OUT.partial"

log "pg_dump $DB_NAME -> $OUT"
# -Fc: сжатый custom-формат, восстанавливается через pg_restore (гибко, по таблицам).
if docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$TMP"; then
  mv "$TMP" "$OUT"
  SIZE="$(du -h "$OUT" | cut -f1)"
  log "готово: $OUT ($SIZE)"
else
  rm -f "$TMP"
  log "ОШИБКА: pg_dump завершился неуспешно"
  exit 1
fi

# Ретеншен: удалить дампы старше N дней.
DELETED="$(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.dump" -type f -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"
log "ретеншен: удалено старых дампов: ${DELETED} (хранится ${RETENTION_DAYS} дн.)"

COUNT="$(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.dump" -type f | wc -l)"
log "всего дампов в $BACKUP_DIR: ${COUNT}"

# Состояние движков (.fastresume/.torrents/.state). Запускаем внутри контейнера api:
# там уже есть реестр движков, TLS-сертификаты и токен внутреннего API, поэтому не нужно
# заводить SSH-доступ с CT400 на хосты движков. Свой ретеншен модуль применяет сам.
# Неуспех здесь не должен ронять весь бэкап — дамп БД уже снят и это главное.
if docker inspect "$API_CONTAINER" >/dev/null 2>&1; then
  log "метаданные движков -> $BACKUP_DIR/engines"
  if docker exec "$API_CONTAINER" python3 -m seeding_api.backup_engines 2>&1 | sed "s/^/  /"; then
    log "метаданные движков: готово"
  else
    log "ПРЕДУПРЕЖДЕНИЕ: метаданные движков сняты не полностью (см. выше)"
  fi
else
  log "ПРЕДУПРЕЖДЕНИЕ: контейнер api '$API_CONTAINER' не найден — метаданные движков пропущены"
fi
