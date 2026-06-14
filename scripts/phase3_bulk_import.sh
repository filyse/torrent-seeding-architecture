#!/bin/sh
# Массовый импорт .torrent через API upload. Распределение round-robin по b1–b6.
# Usage: bash scripts/phase3_bulk_import.sh [PER_ENGINE] [TORRENT_DIR]
set -e
PER_ENGINE="${1:-10}"
TORRENT_DIR="${2:-/mnt/media/seeding-test/torrent-import}"
API="${SEEDING_API:-http://127.0.0.1:8000}"
TOTAL=$((PER_ENGINE * 6))

if [ ! -d "$TORRENT_DIR" ]; then
  echo "missing directory: $TORRENT_DIR" >&2
  exit 1
fi

count=0
ok=0
fail=0

for f in "$TORRENT_DIR"/*.torrent; do
  [ -f "$f" ] || continue
  [ "$count" -ge "$TOTAL" ] && break
  engine_idx=$((count % 6 + 1))
  bn="b${engine_idx}"
  base=$(basename "$f" .torrent)
  save_path="/data/${bn}/phase3/${base}"
  display_name="$base"
  code=$(curl -s -o /tmp/phase3_upload.json -w '%{http_code}' \
    -X POST "$API/api/v1/torrents/upload" \
    -F "torrent_file=@${f}" \
    -F "save_path=${save_path}" \
    -F "display_name=${display_name}")
  if [ "$code" = "201" ]; then
    ok=$((ok + 1))
    id=$(python3 -c "import json; print(json.load(open('/tmp/phase3_upload.json'))['id'])" 2>/dev/null || echo "?")
    echo "OK $bn id=$id $base"
  else
    fail=$((fail + 1))
    echo "FAIL $bn http=$code $base" >&2
  fi
  count=$((count + 1))
  sleep 0.2
done

echo "=== import done: ok=$ok fail=$fail requested=$TOTAL ==="
