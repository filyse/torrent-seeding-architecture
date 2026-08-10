#!/usr/bin/env bash
# Восстановление Postgres из дампа, снятого db-backup.sh (-Fc / pg_restore).
# ВНИМАНИЕ: перезаписывает данные в БД (--clean). Останови api/queue_worker перед запуском.
#
# Использование:
#   scripts/db-restore.sh /mnt/media/seeding-test/_backups/db/seeding-YYYYMMDD-HHMMSS.dump
set -euo pipefail

DUMP="${1:-}"
DB_CONTAINER="${DB_CONTAINER:-containerd-db-1}"
DB_USER="${POSTGRES_USER:-seeding}"
DB_NAME="${POSTGRES_DB:-seeding}"

if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  echo "ОШИБКА: укажи существующий файл дампа. Доступные:" >&2
  ls -1t "${DB_BACKUP_DIR:-/mnt/media/seeding-test/_backups/db}"/*.dump 2>/dev/null | head -n 10 >&2 || true
  exit 1
fi

echo "Восстановление '$DB_NAME' из $DUMP"
read -r -p "Это перезапишет текущие данные. Продолжить? [y/N] " ans
case "$ans" in
  y|Y|yes|YES) ;;
  *) echo "Отменено."; exit 0 ;;
esac

# pg_restore с --clean --if-exists пересоздаёт объекты. Читаем дамп из stdin.
docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner < "$DUMP"
echo "Готово. Перезапусти api и queue_worker:"
echo "  scripts/deploy-ct400.sh restart api queue_worker"
