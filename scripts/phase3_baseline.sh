#!/bin/sh
# Снимок метрик CT 400 для фазы 3. Запуск: bash scripts/phase3_baseline.sh
set -e
API="${SEEDING_API:-http://127.0.0.1:8000}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "=== phase3 baseline $TS ==="
echo "--- health ---"
curl -sf "$API/api/v1/health" || echo '{"error":"health failed"}'
echo
echo "--- torrent count ---"
curl -sf "$API/api/v1/torrents" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo 0
echo "--- memory ---"
free -h
echo "--- media disk ---"
df -h /mnt/media/seeding-test/b1 2>/dev/null || df -h /mnt/media 2>/dev/null || true
echo "--- docker stats ---"
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | grep -E 'engine-b|api|queue|NAME' || true
echo "--- engine torrents (internal list) ---"
for n in 1 2 3 4 5 6; do
  c=$(docker exec "containerd-engine-b${n}-1" python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('http://127.0.0.1:8081/internal/v1/torrents', timeout=5)
    print(len(json.load(r)))
except Exception:
    print('-')
" 2>/dev/null || echo "-")
  echo "b$n: $c"
done
echo "=== end ==="
