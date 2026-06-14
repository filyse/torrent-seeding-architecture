# Фаза 3 — нагрузочный тест (CT 400)

Цель: проверить multi-engine под нагрузкой announce/restore **до** миграции на seedbox. Flood на `.171` не трогаем.

## Предусловия

- Стек: `docker compose -f docker-compose.multi-engine.yml -f docker-compose.multi-engine.media.yml up -d`
- Media: `/mnt/media` (~2 TB свободно), данные движков в `/mnt/media/seeding-test/b1`…`b6`
- WAN: DNAT `50001–50006` → `192.168.1.101` (роутер R1)
- Engine image с `ca-certificates` (HTTPS-трекеры)

## Этапы

| Этап | Торрентов/движок | Цель |
|------|------------------|------|
| 3.1 Baseline | 0 | RAM, CPU, latency `/health` |
| 3.2 Pilot | 10 | импорт, announce, ошибки трекеров |
| 3.3 Medium | 50 | стабильность, swap |
| 3.4 Target | 100–200 | целевая нагрузка (при RAM ≥ 4 GB) |

При **2 GB RAM** CT — не выше **50/движок** без увеличения памяти.

## Скрипты

```bash
# На CT 400 (или через pct exec)
cd /opt/containerd

# 1) Снимок метрик
bash scripts/phase3_baseline.sh

# 2) Подготовить .torrent (пример: из minio)
mkdir -p /mnt/media/seeding-test/torrent-import
ls /mnt/media/minio/webscreenshot/torrents/*.torrent | shuf | head -60 \
  | xargs -I{} cp -n {} /mnt/media/seeding-test/torrent-import/

# 3) Импорт (10 на движок = 60 всего)
bash scripts/phase3_bulk_import.sh 10

# 4) Снова baseline через 5–10 мин
bash scripts/phase3_baseline.sh

# 5) Restore после рестарта
docker compose -f docker-compose.multi-engine.yml -f docker-compose.multi-engine.media.yml restart engine-b1
curl -s -X POST http://127.0.0.1:8000/api/v1/jobs/restore-all
```

## Метрики (отчёт)

- `api /health` — все движки ok
- `docker stats` — CPU/RAM на engine-b*
- Число торрентов в БД / в runtime (по движкам)
- Sample `GET /internal/v1/torrents/{id}/debug` — `start_sent`, fails, SSL
- Swap usage (`free -h`)
- Время `restore-all` job

Шаблон отчёта: `docs/reports/YYYY-MM-DD-phase3.md`

## Пути save_path

Импорт кладёт файлы в `/data/bN/phase3/<имя>` — маршрутизация по `storage_prefix` корректна.

Для **restore-from-disk** существующего контента на library (позже):

- В compose: read-only `/mnt/media` → `/media` в движке
- `save_path`: `/media/TVShows/...` (путь внутри контейнера)
