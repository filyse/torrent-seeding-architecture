# API (`seeding_api`)

Публичный HTTP для `web/` и `desktop/`.

Поддерживаются оба сценария добавления на сидирование:

- `POST /api/v1/torrents` — по `magnet_uri`
- `POST /api/v1/torrents/upload` — multipart upload `.torrent` файла (`torrent_file`, `save_path`, `display_name?`)

## Разработка

Из корня этого репозитория (после `pip install -e ./db`):

```bash
pip install -e ./api
uvicorn seeding_api.main:app --reload --port 8000
```

Переменные окружения (см. `docker-compose.yml`):

- `DATABASE_URL` — async SQLAlchemy URL
- `ENGINE_URL` — базовый URL внутреннего HTTP движка
- `REDIS_URL` — для постановки задач в очередь (ARQ); без него `POST /api/v1/jobs/noop` и `POST /api/v1/jobs/engine-health-check` вернут 503
- `REDIS_URL` — для постановки задач в очередь (ARQ); без него `POST /api/v1/jobs/noop`, `POST /api/v1/jobs/engine-health-check`, `POST /api/v1/jobs/sync-runtime` вернут 503
- `CORS_ORIGINS` — список через запятую для продакшена
- `SEEDING_AUTO_SCHEMA` — если `1`/`true`/`yes`, при старте вызывается `init_models` (только для **локальных тестов** и быстрого dev на SQLite; в Docker прод используйте Alembic из `api` Dockerfile)
- `SEEDING_ENGINE_RESTORE` — если `0`/`false`/`no`, отключить восстановление торрентов в движке при старте API (по умолчанию включено: читает БД и вызывает `ENGINE_URL` для строк со статусами `downloading` / `seeding` / `paused`)
- `SEEDING_API_KEYS` — необязательно: список ключей через **запятую**. Если задан, для маршрутов `/api/v1/torrents/*` и `/api/v1/jobs/*` нужен заголовок **`X-API-Key`**. `/api/v1/health` и корень `/` без ключа.
- `SEEDING_REQUIRE_ENGINE_FOR_DELETE` — если `1`/`true`/`yes`, при `DELETE /api/v1/torrents/{id}` без успешного ответа движка возвращается **502** и строка в БД **не** удаляется. По умолчанию (переменная не задана) при ошибке HTTP к движку строка в БД всё равно удаляется (**204**), в лог пишется предупреждение.

Удаление торрента: **`DELETE /api/v1/torrents/{id}`** — сначала вызов движка `DELETE /internal/v1/torrents/{id}`, затем удаление строки в БД (см. `SEEDING_REQUIRE_ENGINE_FOR_DELETE` при недоступном движке).

## Формат ошибок

Единый JSON:

```json
{
  "error": {
    "code": 404,
    "message": "torrent not found"
  }
}
```

Для ошибок валидации Pydantic (`422`):

```json
{
  "error": {
    "code": 422,
    "message": "validation_failed",
    "fields": [ ... ]
  }
}
```

Реализация: `seeding_api/main.py` — обработчики `HTTPException` и `RequestValidationError`.

## План расширения

См. раздел «Агент 4» в [`docs/PLAN_BY_AGENT.md`](../docs/PLAN_BY_AGENT.md).
