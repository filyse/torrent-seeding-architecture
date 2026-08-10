#!/bin/sh
# Замер self-restore: рестарт движка и время до полного восстановления раздач из fastresume.
# Usage: bash restore_timer.sh N   (N = индекс движка, напр. 6)
N="${1:-6}"
C="containerd-engine-b${N}-1"
count() {
  docker exec "$C" python3 -c "
import ssl,urllib.request,json,os
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
r=urllib.request.Request('https://127.0.0.1:8081/internal/v1/torrents',headers={'X-Engine-Token':os.environ.get('SEEDING_ENGINE_API_TOKEN','')})
print(len(json.load(urllib.request.urlopen(r,timeout=15,context=ctx))))
" 2>/dev/null || echo -1
}
before=$(count)
echo "before restart: $before torrents"
t0=$(date +%s)
docker restart "$C" >/dev/null
echo "restarted, polling self-restore..."
i=0
while [ "$i" -lt 120 ]; do
  sleep 3
  c=$(count)
  el=$(( $(date +%s) - t0 ))
  echo "  t=${el}s torrents=${c}"
  if [ "$c" -ge "$before" ] && [ "$c" -ge 0 ]; then
    echo "RESTORED $c torrents in ~${el}s"
    break
  fi
  i=$((i+1))
done
