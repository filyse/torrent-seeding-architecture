#!/bin/sh
set -e
ROOT="${SEEDING_DATA_ROOT:-/data}"

# SEEDING_STORAGE_REQUIRE_MOUNT=1: не создавать каталог раздачи и не стартовать,
# пока он не отдельный том. Иначе после обрыва питания (fstab nofail) Docker
# видит пустой каталог на системном диске и libtorrent качает библиотеку заново.
require_storage_mount() {
	case "$(printf '%s' "${SEEDING_STORAGE_REQUIRE_MOUNT:-0}" | tr '[:upper:]' '[:lower:]')" in
	1|true|yes|on) ;;
	*) return 0 ;;
	esac
	if [ -z "${ENGINE_STORAGE_SUBDIR:-}" ]; then
		echo "SEEDING_STORAGE_REQUIRE_MOUNT=1, но ENGINE_STORAGE_SUBDIR пуст" >&2
		exit 1
	fi
	content="$ROOT/$ENGINE_STORAGE_SUBDIR"
	if [ ! -d "$content" ]; then
		echo "диск раздачи $content не смонтирован (каталога нет). Движок не стартует." >&2
		exit 1
	fi
	root_dev=$(stat -c %d "$ROOT" 2>/dev/null || stat -f %d "$ROOT")
	content_dev=$(stat -c %d "$content" 2>/dev/null || stat -f %d "$content")
	if [ "$root_dev" = "$content_dev" ]; then
		echo "диск раздачи $content не смонтирован: тот же filesystem, что и $ROOT. Движок не стартует." >&2
		exit 1
	fi
}

require_storage_mount
mkdir -p "$ROOT/.state" "$ROOT/.fastresume" "$ROOT/.torrents"
case "$(printf '%s' "${SEEDING_STORAGE_REQUIRE_MOUNT:-0}" | tr '[:upper:]' '[:lower:]')" in
1|true|yes|on) ;;
*)
	if [ -n "${ENGINE_STORAGE_SUBDIR:-}" ]; then
		mkdir -p "$ROOT/$ENGINE_STORAGE_SUBDIR"
	fi
	;;
esac

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
