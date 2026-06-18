#!/bin/sh
# Снимок ресурсов CT400 под нагрузкой. Запуск: bash loadstat.sh [LABEL]
LABEL="${1:-snapshot}"
echo "================ $LABEL  $(date -u +%H:%M:%S) ================"

echo "--- memory (MB) ---"
free -m | awk 'NR==1 || /Mem|Swap/'

echo "--- cgroup rss / working_set (via cadvisor metrics) ---"
docker exec containerd-prometheus-1 sh -c 'wget -qO- "http://localhost:9090/api/v1/query?query=container_memory_rss%7Bid%3D%22/%22%7D" 2>/dev/null' \
  | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); v=float(d['data']['result'][0]['value'][1]); print(f'rss={v/1048576:.0f} MB')
except Exception: print('rss=?')" 2>/dev/null

echo "--- docker stats (engines/api/queue) ---"
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' \
  | grep -E 'engine-b|containerd-api|queue' | sort

echo "--- per-engine: torrents / open fds ---"
total=0
for n in 1 2 3 4 5 6; do
  c=$(docker exec "containerd-engine-b${n}-1" python3 -c "
import ssl,urllib.request,json,os
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
r=urllib.request.Request('https://127.0.0.1:8081/internal/v1/torrents',headers={'X-Engine-Token':os.environ.get('SEEDING_ENGINE_API_TOKEN','')})
print(len(json.load(urllib.request.urlopen(r,timeout=10,context=ctx))))
" 2>/dev/null || echo "-")
  fds=$(docker exec "containerd-engine-b${n}-1" sh -c 'ls /proc/1/fd 2>/dev/null | wc -l' 2>/dev/null || echo "-")
  echo "b$n: torrents=$c fds=$fds"
  case "$c" in ''|*[!0-9]*) ;; *) total=$((total+c)) ;; esac
done
echo "TOTAL runtime torrents: $total"
echo
