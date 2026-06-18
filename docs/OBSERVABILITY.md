# Наблюдаемость (Фаза 6)

Метрики Prometheus, дашборд Grafana, структурные логи и алерты.

## Метрики

API отдаёт метрики в текстовом формате экспозиции Prometheus:

- `GET /metrics` — корневой путь (для скрейпа внутри docker-сети);
- `GET /api/v1/metrics` — тот же ответ через основной префикс (доступен снаружи через Caddy).

Опциональная защита токеном: задайте `SEEDING_METRICS_TOKEN`, тогда скрейпер должен слать
`Authorization: Bearer <token>` либо `?token=<token>`. По умолчанию эндпоинт открыт.

Основные ряды:

| Метрика | Тип | Описание |
|---|---|---|
| `seeding_build_info{version}` | gauge | Версия API |
| `seeding_database_up` | gauge | Доступность PostgreSQL (1/0) |
| `seeding_torrents{status}` | gauge | Число раздач по статусу (из БД) |
| `seeding_torrents_total_count` | gauge | Всего логических раздач |
| `seeding_engine_up{engine}` | gauge | Движок доступен (1/0) |
| `seeding_engine_torrents{engine}` / `_torrents_active{engine}` | gauge | Раздачи/активные на движке |
| `seeding_engine_download_rate_bytes{engine}` / `_upload_rate_bytes{engine}` | gauge | Скорости движка |
| `seeding_engine_disk_total_bytes{engine}` / `_disk_free_bytes{engine}` | gauge | Диск движка |
| `seeding_engine_peers{engine}` / `_seeds{engine}` / `_dht_nodes{engine}` | gauge | Связность |
| `seeding_engine_torrent_errors{engine}` | gauge | Раздачи в ошибке на движке |
| `seeding_engine_uploaded_bytes_total{engine}` / `_downloaded_bytes_total{engine}` | counter | Накопленный объём |
| `seeding_queue_up` | gauge | Очередь ARQ доступна |
| `seeding_queue_report_age_seconds` | gauge | Возраст последнего отчёта воркера |
| `seeding_queue_jobs{state}` | gauge | Счётчики задач ARQ |
| `seeding_restore_duration_seconds` / `_restore_torrents_total` / `_restore_age_seconds` | gauge | Restore при старте API |

## Стек Prometheus + Grafana

Опциональный оверлей `docker-compose.observability.yml`. Запуск вместе с основным compose:

```bash
docker compose \
  -f docker-compose.multi-engine.yml \
  -f docker-compose.multi-engine.media.yml \
  -f docker-compose.observability.yml up -d prometheus grafana
```

- Prometheus: `:9090`, скрейпит `api:8000/metrics` (15 c), грузит правила `observability/alerts.yml`.
- Grafana: `:3001` (логин `admin`/`admin`, переопределяется `GRAFANA_USER`/`GRAFANA_PASSWORD`).
  Датасорс и дашборд «Torrent Seeding» подключаются автоматически (provisioning).

## Структурные логи

`SEEDING_LOG_JSON=1` (включён в compose для api/engine/queue) переводит логи в построчный
JSON, включая логи доступа uvicorn. Уровень — `LOG_LEVEL` (по умолчанию INFO). Поля:
`ts, level, service, logger, msg` (+ `exc` при ошибке).

## Алерты

Встроенный модуль вычисляет активные алерты по тем же агрегатам, что и метрики:

- движок недоступен (`engine_down:<id>`, critical);
- БД недоступна (`db_down`, critical);
- очередь зависла/отстаёт (`queue_stale`/`redis_down`, warning);
- есть раздачи в ошибке (`torrents_error`, warning);
- мало места на движке (`disk_low:<id>`, warning) — пороги `SEEDING_DISK_ALERT_PCT`
  (по умолчанию 10%) и/или `SEEDING_DISK_ALERT_GB`.

API: `GET /api/v1/alerts` (operator+). UI: панель «Уведомления» в Настройках с
автообновлением. Если задан `SEEDING_ALERT_WEBHOOK`, фоновый цикл (`SEEDING_ALERT_INTERVAL`,
по умолчанию 60 c) шлёт уведомления при появлении/снятии алерта (JSON `{"text": ...}` —
совместимо со Slack/Mattermost/Discord-вебхуками).

Те же правила продублированы для Prometheus в `observability/alerts.yml` (можно подключить
Alertmanager при необходимости внешней маршрутизации).
