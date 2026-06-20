"""Фоновый снимок «живых» полей рантайма в БД.

Раз в N секунд снимаем рантайм с каждого движка одним запросом (list_runtime) и пишем в
таблицу torrents поля up_rate/down_rate/peers/progress/uploaded_total/size. Это позволяет
сортировать и фильтровать раздачи по активности на стороне БД — глобально и постранично,
не обходя движки на каждый запрос списка.

Пишем только изменившиеся строки, поэтому в стабильном сидбоксе (большинство = 0 отдачи)
нагрузка на запись минимальна.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime

log = logging.getLogger(__name__)


def snapshot_interval() -> int:
    try:
        return max(0, int(os.getenv("SEEDING_RUNTIME_SNAPSHOT_INTERVAL", "10")))
    except ValueError:
        return 10


async def snapshot_once(pool, session_factory, hub=None) -> int:
    """Один проход снимка. Возвращает число обновлённых строк.

    Если передан ``hub`` (WebSocket-хаб, Фаза 7), для раздач, на которые есть активная
    подписка ``torrent:{id}``, публикуем дельту живых полей — чтобы деталь обновлялась
    пушем, а не поллингом. Сборку дельты делаем только для отслеживаемых раздач, поэтому
    на тысячах раздач это почти бесплатно.
    """
    async with session_factory() as session:
        repo = TorrentRepository(session)
        rows = await repo.list_all()
        engine_ids = {r.engine_id for r in rows if r.engine_id}

        async def _fetch(eid: str):
            try:
                return eid, await pool.client_for(eid).list_runtime()
            except Exception:  # noqa: BLE001 — движок недоступен: пропустим его строки
                return eid, None

        results = dict(await asyncio.gather(*(_fetch(e) for e in engine_ids)))

        now = datetime.now(timezone.utc)
        updates: list[dict] = []
        status_updates: list[dict] = []
        ws_deltas: dict[int, dict] = {}
        for r in rows:
            rt_map = results.get(r.engine_id)
            if rt_map is None:
                continue  # движок не ответил — сохраняем последнее известное состояние
            h = rt_map.get(r.id)
            if h is None:
                # раздачи нет в рантайме движка — считаем активность нулевой
                up = down = peers = 0
                prog = r.progress or 0.0
                upl = r.uploaded_total
                sz = r.size
            else:
                up = int(h.get("upload_rate") or 0)
                down = int(h.get("download_rate") or 0)
                peers = int(h.get("peers") or 0)
                prog = float(h.get("progress") or 0.0)
                upl = int(h.get("total_uploaded") or 0)
                sz = int(h.get("size") or 0) or r.size

                # Согласуем статус по рантайму для ВСЕХ раздач (а не только для открытой страницы
                # списка). Иначе импортированные сиды навсегда висят в «downloading» с импорта и
                # счётчики статусов врут. «migrating» не трогаем — он держится до конца переноса.
                if r.status != TorrentStatus.migrating.value:
                    target = status_from_runtime(
                        h.get("runtime_status"), h.get("lt_state"), prog
                    )
                    if target != r.status:
                        status_updates.append({"id": r.id, "status": target})

            changed = (
                up != r.up_rate
                or down != r.down_rate
                or peers != r.peers
                or abs(prog - (r.progress or 0.0)) > 1e-4
                or upl != r.uploaded_total
                or sz != r.size
            )
            if changed:
                updates.append(
                    {
                        "id": r.id,
                        "up_rate": up,
                        "down_rate": down,
                        "peers": peers,
                        "progress": prog,
                        "uploaded_total": upl,
                        "size": sz,
                        "runtime_at": now,
                    }
                )

            if hub is not None and hub.has_subscribers(f"torrent:{r.id}"):
                ws_deltas[r.id] = {
                    "id": r.id,
                    "up_rate": up,
                    "down_rate": down,
                    "peers": peers,
                    "progress": prog,
                    "uploaded_total": upl,
                    "size": sz,
                }

        for su in status_updates:
            if su["id"] in ws_deltas:
                ws_deltas[su["id"]]["status"] = su["status"]

        await repo.bulk_update_runtime(updates)
        await repo.bulk_update_status(status_updates)
        await session.commit()

        if hub is not None and ws_deltas:
            for tid, payload in ws_deltas.items():
                await hub.publish(f"torrent:{tid}", payload)

        return len(updates) + len(status_updates)


async def runtime_snapshot_loop(pool, session_factory, hub=None) -> None:
    interval = snapshot_interval()
    if interval <= 0:
        log.info("runtime snapshot loop disabled (interval=0)")
        return
    log.info("runtime snapshot loop started (interval=%ss)", interval)
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                n = await snapshot_once(pool, session_factory, hub=hub)
                if n:
                    log.debug("runtime snapshot: updated %s rows", n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("runtime snapshot failed: %s", exc)

            # WS-канал агрегатов сессии: публикуем только если кто-то смотрит панель.
            if hub is not None and hub.has_subscribers("stats"):
                try:
                    by_engine = await pool.session_stats_all()
                    await hub.publish("stats", {"stats": pool.aggregate_session_stats(by_engine)})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.debug("ws stats publish failed: %s", exc)
    except asyncio.CancelledError:
        raise
