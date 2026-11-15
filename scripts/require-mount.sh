#!/bin/sh
# Выход 0, если каталог контента — отдельный том, не тот же filesystem, что родитель.
# Выход 1, если диска ещё нет: контейнер с restart сам повторит старт.
#
#   sh scripts/require-mount.sh /data/c1 /data
#
# Для FTP и других контейнеров, которые bind-mount'ят тот же путь, что и движок.
# Движок делает ту же проверку сам при SEEDING_STORAGE_REQUIRE_MOUNT=1.
set -e
content="${1:?content path}"
parent="${2:?parent path}"
if [ ! -d "$content" ]; then
  echo "диск $content не смонтирован (каталога нет)" >&2
  exit 1
fi
content_dev=$(stat -c %d "$content" 2>/dev/null || stat -f %d "$content")
parent_dev=$(stat -c %d "$parent" 2>/dev/null || stat -f %d "$parent")
if [ "$content_dev" = "$parent_dev" ]; then
  echo "диск $content не смонтирован: тот же filesystem, что и $parent" >&2
  exit 1
fi
