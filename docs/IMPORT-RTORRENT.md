# Импорт раздач из rtorrent/ruTorrent в движок (без копирования данных)

Перенос «живых» раздач с существующего сидбокса rtorrent/ruTorrent в наш движок так,
чтобы движок сидировал **тот же контент на месте** (read-only), а rtorrent эти раздачи
**остановил** (обратимо, без удаления). Реально применено для бакета `b1` на
`192.168.1.171` (1720 раздач, ~контент в `/home/rudub/storage/b1`).

## Идея

- Контент rtorrent лежит на хосте в `/home/rudub/storage/<bucket>` (`b1..b6`),
  внутри rtorrent это `/downloads/<bucket>/<Название>`.
- Монтируем `/home/rudub/storage/<bucket>` в контейнер движка **read-only** на
  `/data/<bucket>` (совпадает со `storage_prefix` движка) — движок никогда не пишет в контент.
- Регистрируем каждую раздачу в оркестраторе на нужном движке с `seed_mode=true`:
  libtorrent считает данные готовыми и хеширует кусок **лениво** при первом запросе пира —
  **без полной перепроверки** (для ~15 ТБ это критично: иначе дни чтения диска).
- После подтверждённого сидирования останавливаем раздачи в rtorrent (`d.stop`+`d.close`),
  чтобы не было двойного сидирования. Обратимо: `d.start`.

`save_path` в координатах движка:
- multi-file → `/data/<bucket>` (libtorrent сам добавит подпапку `<name>`);
- single-file → `/data/<bucket>/<folder>` (файл лежит прямо в этой папке).

## Поддержка в коде

- Движок: `add_torrent(..., seed_mode=bool)` ставит `torrent_flags.seed_mode`
  (`engine/seeding_engine/torrent_runtime.py`), проброшено через внутренний API
  (`POST /internal/v1/torrents`, поле `seed_mode`).
- Оркестратор: `POST /api/v1/torrents/upload` принимает форму `seed_mode`, прокидывает
  в `EngineClient.register_torrent_file(..., seed_mode=...)`.

## Подключение контента к движку (host-local override)

Файл `docker-compose.b1-content.yml` рядом с `docker-compose.engine.yml` (НЕ в git,
host-specific, как `.env.engine`):

```yaml
services:
  engine:
    volumes:
      - /home/rudub/storage/b1:/data/b1:ro
```

Пересоздать движок:

```bash
cd ~/seeding-engine
docker compose --env-file .env.engine \
  -f docker-compose.engine.yml -f docker-compose.b1-content.yml up -d --build
```

## Процедура

1. **API-ключ.** На control-plane выпустить временный admin-ключ (по завершении удалить).
2. **Импорт** (на хосте сидбокса):

   ```bash
   ORCH=http://192.168.1.101:8000 API_KEY=sk_... BUCKET=b1 DRY=1 LIMIT=4 \
     python3 scripts/import-rtorrent/import_from_rtorrent.py   # dry-run, проверить маппинг
   ORCH=... API_KEY=... BUCKET=b1 LIMIT=0 DRY=0 \
     python3 scripts/import-rtorrent/import_from_rtorrent.py   # боевой; пишет /tmp/b1_imported.tsv
   ```

   Идемпотентно (повторный запуск пропускает уже импортированные по `/tmp/<bucket>_imported.tsv`).
3. **Проверка движка.** Все раздачи `lt_state=seeding`; на одной сделать форс-recheck —
   прогресс должен дойти до 100% без ошибок (подтверждает корректность путей).
   Трекеры: `verified=True`.
4. **Остановка в rtorrent** (внутри контейнера rtorrent; скопировать туда скрипт и
   `/<bucket>_imported.tsv`):

   ```bash
   BUCKET=b1 python3 /tmp/stop_rtorrent_bucket.py dry    # проверить: все под /downloads/b1
   BUCKET=b1 python3 /tmp/stop_rtorrent_bucket.py stop   # d.stop + d.close
   ```

   Защита: каждая раздача повторно проверяется на `d.directory` под `/downloads/<bucket>`.
5. **Финальная проверка.** В rtorrent `view.size stopped` == число bucket-раздач, остальные
   бакеты не тронуты; движок продолжает сидировать.

## Откат

- В rtorrent вернуть сидирование: `d.start <hash>` (или из ruTorrent).
- В нашей системе удалить импортированные раздачи (по `label=<bucket>`), движок снять с RO-маунта.

## Результат для b1 (2026-06)

- 1720 раздач импортированы (`ok=1719, skip=1, fail=0`), движок b1 на `.171`: 1720
  `seeding`, RAM ~2 ГБ. В rtorrent b1 — 1720 `stopped`, b2..b6 не тронуты (все `started`).
