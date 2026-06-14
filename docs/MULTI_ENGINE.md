# Multi-engine (CT 400 и seedbox)

## Цель

Обойти узкое горлышко одного libtorrent-процесса:

| Проблема | Решение |
|----------|---------|
| 8600 announce в одном процессе | N движков × ~1400 торрентов |
| HTTP timeout к трекерам | `LT_CONNECTIONS_LIMIT` на движок |
| Один BT-порт | `listen_port` на движок (50001–50006) |
| API restore блокирует старт | Параллельный restore по `engine_id` |
| Синхронный API→engine | Bulk через ARQ (`bulk_register`, `restore_engine`) |

## Роли

- **Control plane** (CT 400): `api`, `db`, `redis`, `queue_worker`, `web`
- **Data plane**: контейнеры `engine-b1` … `engine-b6` (libtorrent + том `/data/bN`)

Маршрутизация: `save_path` → самый длинный `storage_prefix` → `engine_id`.

Пример: `/data/b3/movies/foo` → движок `b3`.

## Конфигурация

`config/engines.ct400.json` или env:

```bash
ENGINES_CONFIG='[{"id":"b1","url":"http://engine-b1:8081","storage_prefix":"/data/b1","listen_port":50001},...]'
# или
ENGINES_CONFIG_FILE=/config/engines.json
```

Fallback (один движок): `ENGINE_URL` + `SEEDING_DATA_ROOT`.

## Фаза 2 — реальная раздача (реализовано)

| Компонент | Настройка |
|-----------|-----------|
| libtorrent | `SEEDING_ENGINE_BACKEND=libtorrent` в образе и compose |
| fastresume | `/data/.fastresume/{db_id}.fastresume` per engine |
| session state | `SEEDING_LT_STATE_FILE=/data/.state/session.state` |
| тома | `seeding_b1` … `seeding_b6` — отдельный Docker volume на движок |

Healthcheck движка проверяет `backend == libtorrent`.

## Запуск на CT 400

```bash
docker compose -f docker-compose.multi-engine.yml up -d --build
```

Проверка:

```bash
curl -s http://127.0.0.1:8000/api/v1/engines
curl -s http://127.0.0.1:8000/api/v1/health
docker compose -f docker-compose.multi-engine.yml exec engine-b1 python -c "import urllib.request,json; print(json.loads(urllib.request.urlopen('http://127.0.0.1:8081/health').read()))"
```

Добавление торрента (попадёт на b2):

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/torrents \
  -H 'Content-Type: application/json' \
  -d '{"save_path":"/data/b2/test","magnet_uri":"magnet:?xt=urn:btih:..."}'
```

## Фоновые задачи

| POST | Задача |
|------|--------|
| `/api/v1/jobs/restore-engine/{id}` | restore одного движка |
| `/api/v1/jobs/restore-all` | restore всех |
| `/api/v1/jobs/bulk-register/{id}` | queued → engine |

## Миграция на seedbox (позже)

1. Тот же `ENGINES_CONFIG`, URL движков → `http://192.168.1.171:808x` или docker на .171
2. R1: DNAT `50001–50006` → хост с движками
3. Flood не трогаем до готовности пула

## Переменные

| Env | Default | Назначение |
|-----|---------|------------|
| `SEEDING_RESTORE_CONCURRENCY` | 32 | параллельность restore в API |
| `SEEDING_BULK_CONCURRENCY` | 16 | bulk в queue worker |
| `LT_CONNECTIONS_LIMIT` | — | лимит соединений libtorrent |
