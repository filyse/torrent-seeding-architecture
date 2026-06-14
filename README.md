# Torrent seeding platform

Репозиторий платформы раздачи торрентов (сидирование), не создание `.torrent`.

## Структура

| Каталог | Назначение |
|---------|------------|
| [`api/`](api/) | HTTP API (FastAPI), команды UI, доступ к БД |
| [`engine/`](engine/) | Процесс с libtorrent (сессия раздачи) |
| [`db/`](db/) | Схемы, миграции, общие модели данных (SQLAlchemy) |
| [`queue/`](queue/) | Фоновые задачи (ARQ + Redis), опционально |
| [`web/`](web/) | Веб-клиент (Vite + TypeScript, в Docker прокси `/api` → `api`) |
| [`desktop/`](desktop/) | Десктоп: CLI `seeding-desktop` + `config.json` (GUI позже) |
| [`docs/`](docs/) | Планы, контракты, отчёты агентов |

## Документы

- [**`docs/BOARD.md`**](docs/BOARD.md) — **план-даска с чекбоксами по каждому агенту** (открывайте в IDE и отмечайте прогресс)
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — архитектура и потоки данных
- [`AGENTS.md`](AGENTS.md) — роли агентов и сдача работ координатору
- [`docs/PLAN_BY_AGENT.md`](docs/PLAN_BY_AGENT.md) — пошаговые планы кода по ролям
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — границы модулей и контракты
- [`docs/QA_MANUAL_CHECKLIST.md`](docs/QA_MANUAL_CHECKLIST.md) — ручные сценарии веб + CLI
- [`docs/reports/`](docs/reports/) — отчёты QA (шаблон в [`AGENTS.md`](AGENTS.md))

## Разворот

```bash
docker compose up --build
```

Сервисы: Postgres, Redis, `api` (:8000), `engine` (:8081, BitTorrent :6881), `queue_worker`, `web` (:3000). На машине без Docker в PATH используйте установленный Docker Desktop.

### Быстрый старт (максимально просто)

- Windows (PowerShell), из корня репозитория:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 up
```

- Linux/macOS:

```bash
bash ./scripts/dev.sh up
```

Полезные команды:

- `...\dev.ps1 status` / `./scripts/dev.sh status` — health API/engine
- `...\dev.ps1 sync` / `./scripts/dev.sh sync` — enqueue `sync_runtime_to_db`
- `...\dev.ps1 test` / `./scripts/dev.sh test` — прогон `pytest`
- `...\dev.ps1 down` / `./scripts/dev.sh down` — остановка compose

### Тест на сервере (VPS)

Нужны **Docker** и **Docker Compose v2**. Из **корня этого репозитория** (после `git clone`):

```bash
docker compose up -d --build
```

Проверки:

```bash
curl -sS http://127.0.0.1:8000/api/v1/health
curl -sS http://127.0.0.1:8081/health   # с хоста, если порт проброшен
```

Веб-интерфейс: `http://<IP-сервера>:3000` (прокси на API внутри compose). Для доступа снаружи откройте в фаерволе **8000** и **3000** (и при реальном BitTorrent — **6881/tcp** и **6881/udp**).

При первом старте API выполняет **`alembic upgrade head`** (см. `api/Dockerfile`). Опционально задайте в `docker-compose.yml` у сервиса `api`: **`SEEDING_API_KEYS`** (и передавайте **`X-API-Key`** с клиента), при строгом удалении без движка — **`SEEDING_REQUIRE_ENGINE_FOR_DELETE=1`**.

Рекомендуется **виртуальное окружение** для локальной разработки Python-модулей (`pip install -e ./db` и т.д.).

## Тесты

Как в CI (нужен пакет `engine` для `tests/test_engine_app.py`):

```bash
pip install -e "./db[dev]" -e "./api[test]" -e "./engine"
pytest
```

Линтер (ставится с `db[dev]`): из корня репозитория выполните `ruff check api db engine queue tests`.

Ожидаемо: **22 passed**, **3 skipped** (интеграция compose без `SEEDING_RUN_COMPOSE_TESTS=1`). Сборка фронта: `cd web && npm install && npm run build` (нужен Node/npm).

При `DELETE /api/v1/torrents/{id}` при сетевой ошибке к движку по умолчанию строка **всё равно удаляется** из БД (**204**); в лог пишется предупреждение (рантайм движка может ещё держать торрент до рестарта). Строгий режим: **`SEEDING_REQUIRE_ENGINE_FOR_DELETE=1`** — тогда **502**, запись не удаляется. См. `docker-compose.yml`.

Интеграция с поднятым **`docker compose`** (опционально):

```bash
set SEEDING_RUN_COMPOSE_TESTS=1
set SEEDING_INTEGRATION_API_URL=http://127.0.0.1:8000
pytest tests/test_compose_integration.py -v
```

## Git и CI

- Это **отдельный** git-репозиторий: ветки и PR — в корне проекта (не внутри `Scripts` и т.п.).
- Клон: **`git clone https://github.com/filyse/torrent-seeding-architecture.git`**
- GitHub Actions: **`.github/workflows/ci.yml`** — **pytest**, **ruff** и **сборка `web`** на push/PR в ветки `main`, `master`, `develop`.

## Правило «без лапши»

- Бизнес-логика раздачи — в `engine/`, не в `api/` (API только оркестрирует и отдаёт состояние).
- Схема БД — только в `db/`; остальные модули импортируют модели/репозитории отсюда.
- Очередь — только в `queue/`; API ставит задачи, не реализует воркеры внутри себя.
- Клиенты (`web/`, `desktop/`) не ходят в БД и не импортируют `engine/` — только в публичный API.
