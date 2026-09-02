# Подключение движка на отдельной машине (быстрый онбординг)

Движок — самостоятельный seeding-узел на libtorrent. Его можно поднять на любой машине,
он сам зарегистрируется в оркестраторе по API-ключу и начнёт принимать раздачи (в т.ч.
переносы по сети). Общий `/media` между машинами не нужен — контент при переносе идёт по
HTTP через оркестратор.

## Предпосылки

- На машине установлены **Docker** и **Docker Compose v2**.
- Машина видит оркестратор по сети (`SEEDING_ORCHESTRATOR_URL`).
- Оркестратор видит машину по адресу `SEEDING_ENGINE_ADVERTISE_URL` (внешний IP + порт API).
- На оркестраторе задан `SEEDING_ENGINE_REGISTER_KEY` (тот же ключ пишем движку).

## Быстрый старт (one-liner)

```bash
git clone https://github.com/filyse/torrent-seeding-architecture.git
cd torrent-seeding-architecture
cp .env.engine.example .env.engine && nano .env.engine   # заполнить значения
./scripts/deploy-engine.sh
```

Скрипт проверит окружение, соберёт образ движка, поднимет контейнер, дождётся `healthy`
и подскажет, как проверить регистрацию.

## Переменные (`.env.engine`)

| Переменная | Обяз. | Назначение |
|---|---|---|
| `SEEDING_ENGINE_ID` | да | Уникальный id движка (не пересекается с b1..b6 и др.) |
| `SEEDING_ORCHESTRATOR_URL` | да | Адрес оркестратора, доступный с этой машины (`http://host:8000`) |
| `SEEDING_ENGINE_REGISTER_KEY` | да | Общий ключ саморегистрации (совпадает с оркестратором) |
| `SEEDING_ENGINE_API_TOKEN` | да | Токен внутреннего API (совпадает с оркестратором), заголовок `X-Engine-Token` |
| `SEEDING_ENGINE_ADVERTISE_URL` | да | Адрес движка для оркестратора — внешний IP + порт API |
| `SEEDING_ENGINE_LISTEN_PORT` | да | BitTorrent-порт (TCP+UDP), открыть для пиров |
| `SEEDING_ENGINE_API_PORT` | нет | Порт внутреннего API на хосте (по умолчанию `8081`) |
| `SEEDING_UPLOAD_TICKET_SECRET` | нет | Секрет HMAC для `/upload/v1`. Если задан — контейнер в сети `seeding-upload` |
| `SEEDING_ENGINE_HEARTBEAT_INTERVAL` | нет | Период heartbeat, сек (по умолчанию `60`) |

`media_path` для удалённого движка **не задаётся**: перенос пойдёт по сети (`transport=http`).
Даже если задать — оркестратор перед локальным переносом проверяет видимость контента по факту
и сам уходит в `http`, если приёмник не видит источник.

## Проверка

```bash
# Регистрация в реестре оркестратора (stale=false, in_pool=true):
curl http://ORCHESTRATOR_HOST:8000/api/v1/engines/registry | grep <id>

# Логи движка:
docker logs -f <id>-seeding
```

После регистрации движок появится в выборе целевого движка при переносе раздачи в веб-UI.

TTL задач creator (по умолчанию 24 ч) чистит RAM на движке и сообщает оркестратору
`POST /api/v1/creator/events/deleted` (тот же `SEEDING_ORCHESTRATOR_URL` и
`X-Register-Key`). Оркестратор публикует Kafka `creator.task.deleted` — вкладка MPW
снимает строку. Новых переменных в `.env.engine` для этого нет.

На время хеша `.torrent` HDD-движок сам режет отдачу до 1 МБ/с (hold в RAM,
не в БД). SSD не трогает. Дефолтов достаточно; override —
`SEEDING_CREATOR_UPLOAD_LIMIT_BPS` / `SEEDING_STORAGE_KIND`. Спека:
[`CREATOR_UPLOAD_HOLD.md`](CREATOR_UPLOAD_HOLD.md).

## Жизненный цикл / выбытие

Движок шлёт heartbeat каждые `SEEDING_ENGINE_HEARTBEAT_INTERVAL` секунд. Если оркестратор не
слышит движок дольше `SEEDING_ENGINE_TTL` (по умолчанию 180с), движок **выбывает** из активного
пула (перестаёт предлагаться для переноса и роутинга). Как только движок снова на связи —
возвращается автоматически. Полный реестр с `last_seen`/`stale` — `GET /api/v1/engines/registry`.

## Безопасность (важно)

Внутренний API движка (`8081`) защищён общим токеном `SEEDING_ENGINE_API_TOKEN`: оркестратор
шлёт его в заголовке `X-Engine-Token`, движок проверяет (сравнение постоянного времени). Без
верного токена все запросы к `/internal/v1/**` отклоняются с `401`. `/health` остаётся открытым
для healthcheck.

Рекомендации:
- Задай длинный случайный токен (одинаковый у оркестратора и всех движков).
- Дополнительно ограничь порт `8081` файрволом, разрешив только IP оркестратора.

### Шифрование канала (TLS)

Внутренний API можно поднять по HTTPS — тогда токен и контент идут в зашифрованном виде, а
оркестратор проверяет серверный сертификат движка по общему CA (защита от подмены/MITM).

1. На машине с CA сгенерируй сертификаты (SAN = имена/IP, по которым оркестратор зовёт движок):

   ```bash
   scripts/gen-certs.sh "DNS:<engine-host>,IP:<engine-ip>"
   ```

   Появятся `certs/ca.crt`, `certs/engine.crt`, `certs/engine.key` (и `ca.key` — храни в секрете,
   на движки не копируй).

2. На машину движка положи `ca.crt`, `engine.crt`, `engine.key` в `certs/`, в `.env.engine` задай
   `SEEDING_ENGINE_TLS=1` и `SEEDING_ENGINE_ADVERTISE_URL=https://<host>:8081`.

3. На оркестраторе задай `SEEDING_ENGINE_TLS_CA=/certs/ca.crt` (смонтируй `ca.crt`) — для статических
   движков укажи `https://` в `engines.json`; для динамических хватает их https advertise-URL.

mTLS (движок дополнительно требует клиентский сертификат оркестратора) — опционально:
`SEEDING_ENGINE_MTLS=1` на движке + `WITH_CLIENT=1 scripts/gen-certs.sh` и
`SEEDING_ENGINE_TLS_CLIENT_CERT`/`_KEY` на оркестраторе.

## Обновление / остановка

```bash
# Обновить код и пересобрать:
git pull && docker compose --env-file .env.engine -f docker-compose.engine.yml up -d --build

# Остановить (данные в volume engine_data сохраняются):
docker compose --env-file .env.engine -f docker-compose.engine.yml down
```
