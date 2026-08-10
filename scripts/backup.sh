#!/usr/bin/env bash
# Бэкап состояния платформы: дамп БД (pg_dump) + метаданные раздач каждого движка
# (.fastresume / .torrents / session.state). Контент НЕ копируется — он большой и
# восстанавливается ре-сидированием при наличии файлов на диске.
#
# Запуск (на хосте CT 400): bash scripts/backup.sh
# Восстановление БД:        gunzip -c db.sql.gz | docker compose ... exec -T db psql -U seeding -d seeding
# Восстановление метаданных: распаковать <engine>-meta.tar.gz в /mnt/media/seeding-test/<engine>/
#
# Переменные окружения (со значениями по умолчанию):
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/containerd}"
BACKUP_ROOT="${BACKUP_ROOT:-/mnt/media/seeding-backups}"
MEDIA_ROOT="${MEDIA_ROOT:-/mnt/media/seeding-test}"
KEEP="${KEEP:-14}"
DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-seeding}"
DB_NAME="${DB_NAME:-seeding}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.multi-engine.yml -f docker-compose.multi-engine.media.yml}"

ts="$(date +%Y%m%d-%H%M%S)"
dest="$BACKUP_ROOT/$ts"
mkdir -p "$dest"

echo "[backup] postgres ($DB_SERVICE/$DB_NAME) -> $dest/db.sql.gz"
( cd "$COMPOSE_DIR" && docker compose $COMPOSE_FILES exec -T "$DB_SERVICE" \
    pg_dump -U "$DB_USER" "$DB_NAME" ) | gzip > "$dest/db.sql.gz"

echo "[backup] engine metadata (.fastresume/.torrents/.state)"
for d in "$MEDIA_ROOT"/*/; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"
  found=0
  for sub in .fastresume .torrents .state; do
    [ -e "$d$sub" ] && found=1
  done
  if [ "$found" -eq 1 ]; then
    tar -C "$d" -czf "$dest/$name-meta.tar.gz" --ignore-failed-read \
      .fastresume .torrents .state 2>/dev/null || true
    echo "  + $name-meta.tar.gz"
  fi
done

echo "[backup] prune: оставляем последние $KEEP"
# shellcheck disable=SC2012
ls -1dt "$BACKUP_ROOT"/*/ 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -rf

echo "[backup] done: $dest"
ls -lh "$dest"
