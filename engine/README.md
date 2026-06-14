# Движок (`seeding_engine`)

HTTP-сервис с **внутренним API** (`/internal/v1/*`) и плuggable-рантаймом раздачи.

## Рантайм

- **`TorrentRuntime`** — абстракция: `start` / `stop`, `add_torrent`, `pause` / `resume`, `get`, `list_all`, `remove`.
- Реализации: **`MockTorrentRuntime`** (in-memory) и **`LibtorrentTorrentRuntime`** (если установлен модуль `libtorrent`).

Выбор бэкенда — переменная **`SEEDING_ENGINE_BACKEND`**:

| Значение   | Поведение |
|------------|-----------|
| `auto`     | libtorrent, если импорт успешен, иначе mock (по умолчанию) |
| `mock`     | всегда mock |
| `libtorrent` | только libtorrent; при отсутствии модуля процесс упадёт при старте |

## Переменные окружения

- **`ENGINE_HTTP_PORT`** — порт HTTP (по умолчанию `8081`).
- **`SEEDING_DATA_ROOT`** — корень данных на диске (логи/состояние; торренты привязываются к `save_path` из API).
- **`SEEDING_LT_STATE_FILE`** — путь к файлу состояния сессии libtorrent (bencode): при старте загружается, при остановке перезаписывается.
- **`SEEDING_FASTRESUME_DIR`** — каталог per-torrent fastresume (по умолчанию `{SEEDING_DATA_ROOT}/.fastresume`). Сохранение при pause/stop/add; загрузка при restore/add если файл `{db_id}.fastresume` есть.
- **`ENGINE_STORAGE_SUBDIR`** — подкаталог на томе движка (`b1`…`b6`), создаётся entrypoint'ом.
- **`LT_LISTEN_PORT_LOW` / `LT_LISTEN_PORT_HIGH`** — диапазон портов BitTorrent для libtorrent (по умолчанию `6881`–`6889`).
- **`LT_ENABLE_DHT`**, **`LT_ENABLE_LSD`**, **`LT_ENABLE_UPNP`**, **`LT_ENABLE_NATPMP`** — `0`/`false`/`no` отключает (по умолчанию включено, кроме явного `0`).
- **`LT_DOWNLOAD_RATE_LIMIT_BPS`** / **`LT_UPLOAD_RATE_LIMIT_BPS`** — лимиты в байтах/с (пусто = не трогать дефолт libtorrent).
- **`LT_CONNECTIONS_LIMIT`** — лимит соединений сессии (если задан и парсится как int).

Применение через `session.apply_settings` или `set_settings` + `session_settings` — зависит от версии биндингов; ошибки логируются как предупреждение.

## Разработка

```bash
pip install -e ./engine
uvicorn seeding_engine.main:app --host 0.0.0.0 --port 8081
```

`GET /health` возвращает в том числе поле **`backend`**: `mock` или `libtorrent`.

## Docker

Образ ставит **`python3-libtorrent`** через apt; по умолчанию **`SEEDING_ENGINE_BACKEND=libtorrent`**. Entrypoint создаёт `/data/.state`, `/data/.fastresume`, `/data/.torrents` и `ENGINE_STORAGE_SUBDIR`.

## Внутренний API

`DELETE /internal/v1/torrents/{db_id}` — убрать торрент из рантайма (строку в БД удаляет публичный API: `DELETE /api/v1/torrents/{id}`).

## Следующие шаги

См. «Агент 5» в [`docs/PLAN_BY_AGENT.md`](../docs/PLAN_BY_AGENT.md): тонкая настройка DHT-роутеров.
