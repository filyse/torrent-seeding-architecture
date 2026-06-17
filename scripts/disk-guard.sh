#!/usr/bin/env bash
# Авто-обслуживание диска на хосте движков/оркестратора:
#  - регулярно чистит docker-мусор (кэш сборки + висячие образы);
#  - следит за свободным местом и пишет предупреждение в лог;
#  - при критическом заполнении делает агрессивную чистку.
#
# Запуск из cron/таймера, напр. ежечасно:
#   0 * * * * /opt/containerd/scripts/disk-guard.sh >/dev/null 2>&1
#
# Пороги настраиваются через env: DISK_WARN_PCT (по умолч. 85), DISK_CRIT_PCT (92),
# DISK_MOUNT (/), DISK_GUARD_LOG (/var/log/disk-guard.log).
set -euo pipefail

THRESHOLD="${DISK_WARN_PCT:-85}"
CRIT="${DISK_CRIT_PCT:-92}"
MOUNT="${DISK_MOUNT:-/}"
LOG="${DISK_GUARD_LOG:-/var/log/disk-guard.log}"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }
used_pct() { df --output=pcent "$MOUNT" 2>/dev/null | tail -1 | tr -dc '0-9'; }

used="$(used_pct)"
log "disk used ${used}% on ${MOUNT}"

# Лёгкая регулярная чистка: только висячие образы и кэш сборки старше 72ч (безопасно).
if command -v docker >/dev/null 2>&1; then
  rec="$(docker builder prune -af --filter 'until=72h' 2>/dev/null | awk '/Total reclaimed/{$1=$1;print}')"
  docker image prune -f >/dev/null 2>&1 || true
  [ -n "${rec:-}" ] && log "builder prune: ${rec}"
fi

used="$(used_pct)"
if [ -n "$used" ] && [ "$used" -ge "$CRIT" ]; then
  log "WARNING: disk ${used}% >= ${CRIT}% (critical) — aggressive docker prune"
  if command -v docker >/dev/null 2>&1; then
    docker image prune -af >/dev/null 2>&1 || true
    docker builder prune -af >/dev/null 2>&1 || true
  fi
  log "after aggressive prune: $(used_pct)%"
elif [ -n "$used" ] && [ "$used" -ge "$THRESHOLD" ]; then
  log "WARNING: disk ${used}% >= ${THRESHOLD}% (warn) — проверь тома движков (docker system df)"
fi
