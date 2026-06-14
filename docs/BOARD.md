# Доска прогресса (план по агентам)

Отмечайте вручную: `[ ]` → `[x]`. Детали шагов — в [`PLAN_BY_AGENT.md`](PLAN_BY_AGENT.md), роли — в [`../AGENTS.md`](../AGENTS.md).

**Рекомендуемый порядок кода:** `6` → `5` и `4` параллельно → `7` → `3` и `2` → на релизе `8` и `9`.

---

## Фаза 0 — координатор

- [x] Утверждён черновик `docs/INTEGRATION.md` (править по мере API/engine)
- [x] Корневой `docker-compose.yml` с сервисами
- [ ] Ветка `develop` и правила PR в **этом** репозитории (отдельный remote)
- [x] CI: `.github/workflows/ci.yml` (pytest + ruff + web build)
- [x] Шаблон отчёта QA в `AGENTS.md`
- [x] Правила Cursor по ролям: `.cursor/rules/*.mdc`

## Агент 1 — координатор (постоянно)

- [x] Актуализировать `INTEGRATION.md` (таблица `/internal/v1`)
- [x] Принимать отчёты из `docs/reports/` — шаблоны заведены (`2026-03-26-qa-agent8.md`, `…-agent9.md`); координатор обновляет по факту прогонов

## Агент 6 — БД (`db/`)

- [x] 6.1 Конфиг URL из env (`seeding_db/config.py`)
- [x] 6.2 Модели торрента (в т.ч. `magnet_uri`, опциональный `info_hash`)
- [x] 6.3 Alembic: начальная миграция
- [x] 6.4 Репозитории: покрыть тестами (pytest + SQLite in-memory)

## Агент 5 — движок (`engine/`)

- [x] 5.1 Точка входа `python -m seeding_engine`
- [x] 5.2 Сессия **libtorrent**: listen + `apply_settings` (DHT/LSD/UPnP/NAT-PMP, лимиты из `LT_*`)
- [x] 5.6 Удаление из рантайма `DELETE /internal/v1/torrents/{id}`; опционально `SEEDING_LT_STATE_FILE` (save/load сессии)
- [x] 5.3 Внутренний HTTP `/internal/v1/*` (пока без libtorrent, in-memory store)
- [x] 5.4 При старте API: восстановление торрентов в движке по БД (`seeding_api/restore.py`, откл. `SEEDING_ENGINE_RESTORE=0`)
- [x] 5.5 Graceful shutdown: uvicorn lifespan → `TorrentRuntime.stop()` (libtorrent: `pause`, при наличии — `abort`)

## Multi-engine (CT 400)

- [x] Реестр движков `ENGINES_CONFIG` / `ENGINES_CONFIG_FILE`
- [x] `engine_id` в БД + миграция `0002`
- [x] `EnginePool` — маршрутизация по `save_path`
- [x] Параллельный restore по движкам
- [x] Bulk jobs: `restore_engine`, `bulk_register`, `restore_all`
- [x] `docker-compose.multi-engine.yml` (b1–b6)
- [x] Документация `docs/MULTI_ENGINE.md`

## Multi-engine фаза 2 — реальная раздача

- [x] libtorrent в Docker-образе (`python3-libtorrent`, `SEEDING_ENGINE_BACKEND=libtorrent`)
- [x] Per-torrent fastresume (`SEEDING_FASTRESUME_DIR`, `fastresume_io.py`)
- [x] `SEEDING_LT_STATE_FILE` per engine в compose
- [x] Отдельные тома `seeding_b1` … `seeding_b6` + `ENGINE_STORAGE_SUBDIR`
- [x] Healthcheck: backend должен быть `libtorrent`

## Агент 4 — API (`api/`)

- [x] 4.1 Каркас FastAPI, `/api/v1/health`, CORS
- [x] 4.2 Подключение БД, lifespan, пул сессий
- [x] 4.3 Клиент к `engine` (httpx), таймауты
- [x] 4.4 Эндпоинты торрентов: список, создать, get, pause, resume, удалить
- [x] 4.5 Зафиксировать формат ошибок в `api/README.md` (синхрон с `main.py`)
- [x] 4.6 `DELETE /api/v1/torrents/{id}`; опционально `SEEDING_API_KEYS` + `X-API-Key`; подтягивание `info_hash` из `runtime` при GET детали

## Агент 7 — очередь (`queue/`)

- [x] 7.1 `WorkerSettings`, Redis из env, Dockerfile + сервис в compose
- [x] 7.2 Вызов `enqueue` из API — `POST /api/v1/jobs/noop` (при `REDIS_URL`)
- [x] 7.3 Идемпотентность `_job_id` — заметка в `queue/README.md`
- [x] 7.4 Задача `check_engine_health` + `POST /api/v1/jobs/engine-health-check`

## Агент 3 — веб (`web/`)

- [x] 3.1 Сборка Vite + TS; база API — относительный `/api/v1` (в Docker прокси в `web/nginx.conf`)
- [x] 3.2 Список и действия; детали по `#/torrent/{id}` (JSON + `runtime`)
- [x] 3.3 Обновление списка (poll ~12 с); SSE/WS — позже
- [x] 3.4 Сообщения для 401/403 + разбор `error.message` в вебе; сеть — текст в `.status`

## Агент 2 — десктоп Windows (`desktop/`)

- [x] 2.1 Каркас: пакет `seeding-desktop`, `config.json`, команда `list` (httpx → `/api/v1/torrents`)
- [x] 2.2 CLI: `add`, `get`, `pause`, `resume`, `remove`, `--api-key` (тот же REST, что веб)
- [x] 2.3 Сборка: `desktop/scripts/build_windows.ps1` + extra `[build]` (PyInstaller)

## Агент 8 — QA интеграция

- [x] 8.1 Opt-in: `tests/test_compose_integration.py` + `SEEDING_RUN_COMPOSE_TESTS=1`
- [x] 8.2 Регрессия контрактов API ↔ engine (mocks `respx` в `tests/test_api_torrents.py`)
- [x] 8.3 Пример отчёта: `docs/reports/2026-03-26-qa-agent8.md`
- [x] 8.4 CI `.github/workflows/ci.yml` (см. фазу 0)

## Агент 9 — QA клиенты

- [x] 9.1 Чеклист: [`docs/QA_MANUAL_CHECKLIST.md`](QA_MANUAL_CHECKLIST.md); отчёт-заготовка [`docs/reports/2026-03-26-qa-agent9.md`](reports/2026-03-26-qa-agent9.md)
- [x] 9.2 Playwright: `web/e2e/smoke.spec.ts`, `npm run test:e2e` (нужны `npx playwright install` и `PLAYWRIGHT_BASE_URL`)
- [x] 9.3 Заготовка отчёта: `docs/reports/2026-03-26-qa-agent9.md`

---

## Сводка по ролям (напоминание)

| № | Агент        | Каталог   |
|---|--------------|-----------|
| 1 | Координатор  | весь репо |
| 2 | Десктоп      | `desktop/` |
| 3 | Веб          | `web/`    |
| 4 | API          | `api/`    |
| 5 | Движок       | `engine/` |
| 6 | БД           | `db/`     |
| 7 | Очередь      | `queue/`  |
| 8 | QA интеграция| `tests/`, compose |
| 9 | QA клиенты   | веб + десктоп |
