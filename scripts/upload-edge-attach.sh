#!/usr/bin/env bash
# Создать сеть seeding-upload и подключить к ней контейнеры {id}-seeding.
set -euo pipefail

NET=seeding-upload
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  docker network create "$NET"
  echo "created network $NET"
else
  echo "network $NET exists"
fi

attached=0
for c in $(docker ps --format '{{.Names}}' | grep -E -- '-seeding$' || true); do
  if docker inspect -f '{{json .NetworkSettings.Networks}}' "$c" | grep -q "\"$NET\""; then
    echo "already on $NET: $c"
    continue
  fi
  docker network connect "$NET" "$c"
  echo "attached $c"
  attached=$((attached + 1))
done

if [[ "$attached" -eq 0 ]]; then
  echo "no new engine containers attached (looked for names ending with -seeding)"
fi
