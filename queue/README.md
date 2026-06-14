# Очередь (`seeding_queue`)

ARQ + Redis: фоновые задачи.

Зарегистрированные jobs:

- `noop_report` — заглушка (`POST /api/v1/jobs/noop`)
- `check_engine_health` — `GET ENGINE_URL/health` (`POST /api/v1/jobs/engine-health-check`)
- `sync_runtime_to_db` — сверка runtime движка с БД (`POST /api/v1/jobs/sync-runtime`):
  - читает `GET ENGINE_URL/internal/v1/torrents`
  - обновляет в БД `status` и `info_hash` по `db_id`
  - возвращает счетчики обновлений и расхождений

## Запуск воркера локально

```bash
pip install -e ./db -e ./queue
export REDIS_URL=redis://localhost:6379/0
arq seeding_queue.worker.WorkerSettings
```

## Docker

См. сервис `queue_worker` в корневом `docker-compose.yml`.

## Идемпотентность (`_job_id`)

При постановке задачи из API, если нужно «не более одного такого job в очереди», передавайте `_job_id` в `enqueue_job` (см. [ARQ](https://github.com/python-arq/arq)). Имя должно быть стабильным (например `recheck-{torrent_id}`).

## План

«Агент 7» в [`docs/PLAN_BY_AGENT.md`](../docs/PLAN_BY_AGENT.md).
