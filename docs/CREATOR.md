# Создание торрентов из контента (RelaySeed «creator»)

Создание `.torrent` прямо из контента, лежащего на диске движка, поверх того же
тома, что движок раздаёт — без копирования файлов. Логика портирована из внешнего
`torrent_api` (libtorrent 1.2) под libtorrent 2.0 (v1-only) и встроена в движок,
поэтому доступна на любом свежеразвёрнутом движке автоматически.

Версии на момент документа: `engine 1.6.0`, `api 1.23.0`, `web 1.44.0`.

## Компоненты и поток

```
web (модал «Создать торрент» / «Очередь создания»)
  → api  /api/v1/creator/*        (оркестратор, роутер creator.py, require_auth)
    → engine /internal/v1/…        (X-Engine-Token, per-engine)
```

- **engine** (`engine/seeding_engine/creator.py`, `CreatorService`): обзор диска,
  постановка задачи, статус/прогресс, отмена, байты `.torrent`. Piece size 16 МБ.
  Проверка последовательности серий (S01E05 / `[05]` / `_05_`) — опциональна, по
  умолчанию **выключена**. Защита от path traversal (`_sanitize` держит путь внутри
  `SEEDING_DATA_ROOT`).
- **Хеширование — в отдельном процессе** (`multiprocessing`, start method `spawn`,
  `_hash_worker`). CPU/IO-bound `set_piece_hashes` держит GIL, поэтому в потоке он
  подвешивал бы событийный цикл движка (опрос статуса/health деградируют, под нагрузкой
  → таймауты «engine unavailable»). Дочерний процесс изолирует эту работу; поток-надзиратель
  (`ThreadPoolExecutor`) лишь перекачивает прогресс/результат через `multiprocessing.Queue`,
  отмену прокидывает через `multiprocessing.Event`. `spawn` (не `fork`) — форкать
  многопоточный процесс движка небезопасно.
- **Параллелизм создания** — env `SEEDING_CREATOR_WORKERS` (сколько дочерних процессов
  хеширования одновременно), **по умолчанию 1**. На HDD параллельное хеширование двух
  папок вызывает seek-thrashing и замедляет обе — последовательно быстрее и ровнее; на
  SSD/NVMe можно поднять.
- **Hold отдачи на HDD.** При старте движок определяет тип тома раздачи
  (`storage_path()` → sysfs `rotational`, не корень `/data` контейнера). HDD и
  `unknown` на время хеша ставят сессионный потолок
  `SEEDING_CREATOR_UPLOAD_LIMIT_BPS` (дефолт 1 МБ/с). Постоянный
  `engines.upload_limit` в БД не меняется: hold в RAM, `set_session_limits`
  во время хеша запоминает новое «куда вернуть» и оставляет кап. SSD — без
  лимита. Override: `SEEDING_STORAGE_KIND=hdd|ssd`; cap `0` — hold выкл.
  В статусе задачи и `session/stats`: `upload_hold` / `creator_upload_hold`.
  Полная спека: [`CREATOR_UPLOAD_HOLD.md`](CREATOR_UPLOAD_HOLD.md).
- **api** (`api/seeding_api/routers/creator.py`): проксирование к движку + два режима:
  - «только создать» — эфемерный `.torrent` стримится в браузер (не хранится);
  - «создать и раздавать» — созданный торрент сразу ставится на сидинг через
    существующий upload-pipeline (`TorrentRepository` + `register_torrent_file`).
- **web** (`web/src/main.ts`): модал создания (движок → папка → мультивыбор → режим →
  прогресс) и «Очередь создания» (список задач со всех движков).

## Эндпоинты

Движок (`/internal/v1`, заголовок `X-Engine-Token`):

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/fs/browse?path=` | листинг каталога относительно `SEEDING_DATA_ROOT` |
| POST | `/creator/tasks` | поставить задачу создания |
| GET | `/creator/tasks` | список всех задач движка (для очереди) |
| GET | `/creator/tasks/{id}` | статус задачи |
| POST | `/creator/tasks/{id}/cancel` | отмена |
| DELETE | `/creator/tasks/{id}` | удалить задачу из очереди/памяти |
| GET | `/creator/tasks/{id}/torrent` | байты `.torrent` |

Оркестратор (`/api/v1/creator`, `require_auth`):

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/browse?engine_id=&path=` | обзор диска движка |
| GET | `/tasks` | **агрегированная очередь по всем движкам** |
| POST | `/tasks` | создать задачу |
| GET | `/tasks/{engine_id}/{id}` | статус |
| POST | `/tasks/{engine_id}/{id}/cancel` | отмена |
| DELETE | `/tasks/{engine_id}/{id}` | удалить задачу из очереди/памяти; в Kafka уходит `creator.task.deleted` |
| GET | `/tasks/{engine_id}/{id}/download` | скачать `.torrent` |
| POST | `/tasks/{engine_id}/{id}/seed` | поставить на раздачу |

Движок → оркестратор (`X-Register-Key`, без `require_auth`):

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/events/deleted` | TTL-reaper: задача стёрта из RAM, API публикует то же Kafka-событие |

Тело события Kafka (`SEEDING_KAFKA_BOOTSTRAP`, топик `SEEDING_KAFKA_TOPIC_CREATOR_DELETED`,
дефолт `creator.task.deleted`):

```json
{"event":"creator.task.deleted","engine_id":"a1","task_id":0,"task_key":"a1:0","reason":"ttl"}
```

`reason`: `deleted` (кнопка / API) или `ttl` (срок жизни задачи). MPW снимает строку
по `task_key`. Без брокера удаление на движке не ломается — пуш просто не уходит.

## Очередь создания — архитектура

**Очередь НЕ на Redis и не в `queue_worker`/arq.** Задачи создания живут
**в памяти движка** (`CreatorService._tasks`, dict), эфемерно — очищаются при
перезапуске движка. UI-«очередь» собирается на лету: оркестратор **параллельно**
(`asyncio.gather`) опрашивает все движки (`pool.specs`) и агрегирует их
`GET /internal/v1/creator/tasks` — один занятый HDD-движок не тормозит всю очередь. Поэтому:

- компонент `queue` (arq/Redis) в фиче не участвует — его версия не меняется;
- после перезапуска движка список его задач создания пуст (готовые `.torrent`, если
  их не скачали/не поставили на раздачу, теряются — это by design, они эфемерны);
- **автоочистка по TTL:** задача живёт не дольше `SEEDING_CREATOR_TASK_TTL` (сек, дефолт
  86400 = 24 ч), затем автоудаляется (фоновый reaper + прунинг при обращении). Reaper
  сообщает оркестратору (`POST /api/v1/creator/events/deleted`), тот публикует
  `creator.task.deleted` в Kafka — вкладка MPW снимает ту же строку. Также
  задачу можно удалить вручную кнопкой «Удалить» (`DELETE …/creator/tasks/{id}`).

## Env hold (кратко)

| Переменная | Дефолт | Смысл |
|------------|--------|--------|
| `SEEDING_CREATOR_UPLOAD_LIMIT_BPS` | `1048576` | потолок отдачи на хеше HDD. `0` — выкл |
| `SEEDING_STORAGE_KIND` | авто по sysfs | `hdd` / `ssd` / `unknown` |

Поля задачи: `upload_hold`. Поля сессии: `disk_kind`, `creator_upload_hold`,
`creator_upload_hold_bps`, `upload_limit_desired`.

## Автопапка движка (web)

Браузер файлов при выборе движка сразу открывает его подпапку контента
(`basename(storage_prefix)`, напр. `/data/a1` → `a1`), а не корень `/data` со
служебными каталогами. При недоступности подпапки — фолбэк на корень.
