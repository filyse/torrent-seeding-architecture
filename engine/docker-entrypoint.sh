#!/bin/sh
set -e
ROOT="${SEEDING_DATA_ROOT:-/data}"
mkdir -p "$ROOT/.state" "$ROOT/.fastresume" "$ROOT/.torrents"
if [ -n "${ENGINE_STORAGE_SUBDIR:-}" ]; then
	mkdir -p "$ROOT/$ENGINE_STORAGE_SUBDIR"
fi

# TLS для внутреннего API (опционально). Включается SEEDING_ENGINE_TLS=1.
# Канал шифруется; оркестратор проверяет серверный сертификат по нашему CA.
if [ "${SEEDING_ENGINE_TLS:-0}" = "1" ]; then
	CERT="${SEEDING_ENGINE_TLS_CERT:-/certs/engine.crt}"
	KEY="${SEEDING_ENGINE_TLS_KEY:-/certs/engine.key}"
	if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
		echo "SEEDING_ENGINE_TLS=1, но не найден сертификат/ключ: $CERT / $KEY" >&2
		exit 1
	fi
	set -- "$@" --ssl-certfile "$CERT" --ssl-keyfile "$KEY"
	# mTLS (опционально): требовать клиентский сертификат, подписанный нашим CA.
	if [ "${SEEDING_ENGINE_MTLS:-0}" = "1" ]; then
		CA="${SEEDING_ENGINE_TLS_CA:-/certs/ca.crt}"
		if [ ! -f "$CA" ]; then
			echo "SEEDING_ENGINE_MTLS=1, но не найден CA: $CA" >&2
			exit 1
		fi
		set -- "$@" --ssl-ca-certs "$CA" --ssl-cert-reqs 2
	fi
fi

exec "$@"
