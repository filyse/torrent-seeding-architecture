#!/bin/sh
set -e
ROOT="${SEEDING_DATA_ROOT:-/data}"
mkdir -p "$ROOT/.state" "$ROOT/.fastresume" "$ROOT/.torrents"
if [ -n "${ENGINE_STORAGE_SUBDIR:-}" ]; then
	mkdir -p "$ROOT/$ENGINE_STORAGE_SUBDIR"
fi
exec "$@"
