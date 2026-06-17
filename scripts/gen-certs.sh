#!/usr/bin/env bash
# Генерация TLS-сертификатов для канала оркестратор ↔ движок.
#
# Создаёт приватный CA (один на кластер) и серверный сертификат движка с нужными SAN.
# CA-ключ (ca.key) держи в секрете и НЕ копируй на машины движков — туда нужны только
# engine.crt + engine.key + ca.crt.
#
# Использование:
#   scripts/gen-certs.sh "DNS:localhost,DNS:engine-b1,...,IP:127.0.0.1,IP:192.168.1.101"
#   CERTS_DIR=certs scripts/gen-certs.sh "<SANs>"        # каталог вывода (по умолчанию ./certs)
#   WITH_CLIENT=1 scripts/gen-certs.sh "<SANs>"          # ещё и клиентский cert для mTLS
#
# SAN обязателен: перечисли все имена/IP, по которым ОРКЕСТРАТОР обращается к движку.
set -euo pipefail

OUT="${CERTS_DIR:-certs}"
SANS="${1:-}"
if [ -z "$SANS" ]; then
  echo "Укажи SAN, напр.: scripts/gen-certs.sh \"DNS:localhost,IP:127.0.0.1,IP:<host-ip>\"" >&2
  exit 1
fi
DAYS_CA="${DAYS_CA:-3650}"
DAYS_CERT="${DAYS_CERT:-825}"

mkdir -p "$OUT"

# --- CA (создаётся один раз; повторный запуск переиспользует существующий) ---
if [ ! -f "$OUT/ca.crt" ] || [ ! -f "$OUT/ca.key" ]; then
  echo "Генерирую CA…"
  openssl genrsa -out "$OUT/ca.key" 4096
  # basicConstraints + keyUsage обязательны: иначе строгая проверка (OpenSSL 3.x)
  # отвергает CA с ошибкой "CA cert does not include key usage extension".
  openssl req -x509 -new -nodes -key "$OUT/ca.key" -sha256 -days "$DAYS_CA" \
    -subj "/CN=seeding-ca" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$OUT/ca.crt"
else
  echo "Использую существующий CA: $OUT/ca.crt"
fi

# --- серверный сертификат движка ---
echo "Генерирую серверный сертификат движка (SAN: $SANS)…"
openssl genrsa -out "$OUT/engine.key" 2048
openssl req -new -key "$OUT/engine.key" -subj "/CN=seeding-engine" -out "$OUT/engine.csr"
{
  echo "subjectAltName=$SANS"
  echo "extendedKeyUsage=serverAuth"
  echo "keyUsage=critical,digitalSignature,keyEncipherment"
  echo "basicConstraints=CA:FALSE"
} > "$OUT/engine.ext"
openssl x509 -req -in "$OUT/engine.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
  -CAcreateserial -days "$DAYS_CERT" -sha256 -extfile "$OUT/engine.ext" -out "$OUT/engine.crt"
rm -f "$OUT/engine.csr" "$OUT/engine.ext"

# --- опционально клиентский сертификат (mTLS) ---
if [ "${WITH_CLIENT:-0}" = "1" ]; then
  echo "Генерирую клиентский сертификат оркестратора (mTLS)…"
  openssl genrsa -out "$OUT/orchestrator.key" 2048
  openssl req -new -key "$OUT/orchestrator.key" -subj "/CN=seeding-orchestrator" -out "$OUT/orchestrator.csr"
  {
    echo "extendedKeyUsage=clientAuth"
    echo "basicConstraints=CA:FALSE"
  } > "$OUT/orchestrator.ext"
  openssl x509 -req -in "$OUT/orchestrator.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
    -CAcreateserial -days "$DAYS_CERT" -sha256 -extfile "$OUT/orchestrator.ext" -out "$OUT/orchestrator.crt"
  rm -f "$OUT/orchestrator.csr" "$OUT/orchestrator.ext"
fi

chmod 600 "$OUT"/*.key 2>/dev/null || true
echo "Готово. Файлы в: $OUT"
ls -1 "$OUT"
