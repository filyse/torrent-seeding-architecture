#!/usr/bin/env bash
set -euo pipefail

CMD="${1:-up}"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-}"

health() {
  local url="$1"
  if curl -fsS "$url" >/dev/null; then
    echo "OK  $url"
  else
    echo "ERR $url"
    return 1
  fi
}

case "$CMD" in
  up)
    docker compose up -d --build
    echo
    echo "Health checks..."
    health "$API_BASE/api/v1/health" || true
    health "http://127.0.0.1:8081/health" || true
    echo
    echo "Web UI: http://127.0.0.1:3000"
    ;;
  down)
    docker compose down
    ;;
  logs)
    docker compose logs -f
    ;;
  test)
    python -m pytest -q
    ;;
  sync)
    if [[ -n "$API_KEY" ]]; then
      curl -fsS -X POST "$API_BASE/api/v1/jobs/sync-runtime" -H "X-API-Key: $API_KEY"
    else
      curl -fsS -X POST "$API_BASE/api/v1/jobs/sync-runtime"
    fi
    echo
    ;;
  status)
    curl -fsS "$API_BASE/api/v1/health" || true
    echo
    curl -fsS "http://127.0.0.1:8081/health" || true
    echo
    ;;
  *)
    echo "Usage: scripts/dev.sh [up|down|logs|test|sync|status]"
    exit 2
    ;;
esac
