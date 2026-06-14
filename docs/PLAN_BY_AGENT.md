# Планы разработки по агентам

Порядок фаз согласует **координатор**; ниже — типовая последовательность, чтобы уменьшить блокировки между модулями.

## Фаза 0 — координатор

1. Утвердить `docs/INTEGRATION.md` (внутренний URL движка, версия `/api/v1/`).
2. Корневой `docker-compose.yml`, healthcheck у `api`/`engine`, CI `.github/workflows/ci.yml` — см. [`docs/BOARD.md`](BOARD.md).
3. Ветка `develop` и правила PR в этом репозитории; шаблон отчёта QA — [`AGENTS.md`](../AGENTS.md).

## Агент 1 (координатор) — непрерывно

- Разруливает пересечения контрактов; мержит после зелёных отчётов **8/9**.
- Держит актуальными `INTEGRATION.md` и этот файл при изменении границ.

---

## Агент 6 — БД (раньше API, параллельно с движком)

**Цель:** единый источник правды о торрентах и настройках.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 6.1 | Пакет `seeding_db`: конфиг подключения из env | тест импорта |
| 6.2 | Модели: торрент (info_hash, путь save_path, статус, display_name, created_at, …) | ревью с API |
| 6.3 | Alembic: начальная миграция | `alembic upgrade head` в CI |
| 6.4 | Репозитории: создание/список/статус/`info_hash`/удаление, выборка для restore | unit-тесты на SQLite in-memory |

Зависимости: нет (первый низкоуровневый слой).

---

## Агент 5 — движок

**Цель:** процесс libtorrent + минимальный внутренний HTTP для команд.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 5.1 | Точка входа `python -m seeding_engine`, чтение env (порт, пути) | контейнер стартует |
| 5.2 | Listen + `LT_*`: DHT/LSD/UPnP/NAT-PMP, rate/connections limits (`apply_settings` / fallback `set_settings`) | env в `engine/README.md` |
| 5.3 | Методы: add magnet/file, pause, resume, remove, status по id | контракт в OpenAPI внутри `engine/` |
| 5.4 | Восстановление в рантайме делает **API** по БД (`restore.py`); движок остаётся stateless между рестартами | тесты `test_api_restore.py` |
| 5.5 | SIGTERM → uvicorn lifespan → `TorrentRuntime.stop()` (libtorrent: pause + `abort` при наличии) | ручной чек в Docker |
| 5.6 | `remove` в рантайме + `DELETE /internal/v1/torrents/{id}`; `SEEDING_LT_STATE_FILE` для save/load сессии | `test_engine_internal_delete_mock` |

Зависимости: согласовать DTO с агентом **4 (API)**.

---

## Агент 4 — API

**Цель:** публичное REST/WebSocket под клиентов.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 4.1 | Каркас FastAPI, `/api/v1/health`, CORS | curl OK |
| 4.2 | Подключение `seeding_db`, lifespan (pool/session) | health с проверкой БД |
| 4.3 | Прокси команд к `engine` (httpx async) с таймаутами | интеграционный тест |
| 4.4 | Торренты: список, создать, детали, пауза, возобновить, удалить; jobs; опционально API keys | OpenAPI `/docs` |
| 4.5 | Ошибки: единый JSON-формат | документ в `api/README.md` |
| 4.6 | `DELETE` торрента; `SEEDING_API_KEYS`; `info_hash` из `runtime` при GET | тесты `test_api_torrents` |

Зависимости: **6** (модели), **5** (живой движок в compose).

---

## Агент 7 — очередь

**Цель:** фоновые задачи без блокировки API.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 7.1 | `WorkerSettings`, Redis из env, образ/команда в compose | worker коннектится к Redis |
| 7.2 | `noop_report` + `check_engine_health` (`GET ENGINE_URL/health`) | `POST /api/v1/jobs/*` |
| 7.3 | Идемпотентность `_job_id` где нужно | описание в README queue |

Зависимости: **4** (кто ставит задачи), **6** (если пишет в БД).

---

## Агент 3 — веб

**Цель:** UI в браузере.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 3.1 | Сборка Vite + TS; база `/api/v1` (dev: прокси Vite; prod: nginx → `api`) | `npm run build` / образ `web` |
| 3.2 | Список + форма + пауза/старт; детали `#/torrent/{id}` + JSON `runtime` | без CORS в Docker |
| 3.3 | Polling ~12 с; позже SSE/WebSocket (когда будет в API) | список сам обновляется |
| 3.4 | 401/403 и `error.message` в UI; сеть — сообщение в статусе | ручной чек при появлении auth |

Зависимости: **4** (стабильные эндпоинты).

---

## Агент 2 — десктоп Windows

**Цель:** нативный клиент, удобные локальные настройки.

| Шаг | Задача | Готово когда |
|-----|--------|----------------|
| 2.1 | Пакет `seeding-desktop`: `config.json`, `python -m seeding_desktop list` | `pip install -e ./desktop` |
| 2.2 | `list`, `add`, `get`, `pause`, `resume`, `remove`, `--api-key` | см. `desktop/README.md` |
| 2.3 | `desktop/scripts/build_windows.ps1`, extra `[build]` (PyInstaller) | `dist/seeding-desktop.exe` |

Зависимости: **4** (тот же контракт, что у веба).

---

## Агент 8 — QA интеграция

| Шаг | Задача |
|-----|--------|
| 8.1 | `SEEDING_RUN_COMPOSE_TESTS=1` + `tests/test_compose_integration.py` (httpx к api/engine) |
| 8.2 | Регрессия контрактов API ↔ engine (мок движка при падении) |
| 8.3 | Отчёт в `docs/reports/` (пример `2026-03-26-qa-agent8.md`) |
| 8.4 | CI: `.github/workflows/ci.yml` в корне репозитория |

---

## Агент 9 — QA клиенты

| Шаг | Задача |
|-----|--------|
| 9.1 | `docs/QA_MANUAL_CHECKLIST.md` (веб + CLI, ошибки сети) |
| 9.2 | Playwright: `web/e2e/smoke.spec.ts`, `PLAYWRIGHT_BASE_URL` |
| 9.3 | `docs/reports/2026-03-26-qa-agent9.md` (шаблон) |

---

## Рекомендуемый порядок старта кода

`6 (БД)` → `5 (движок)` и `4 (API)` параллельно после моделей → `7` → `3` и `2` → `8` и `9` на каждом релиз-кандидате.
