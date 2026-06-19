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

from seeding_db.repository import TorrentRepository

log = logging.getLogger(__name__)


def snapshot_interval() -> int:
    try:
        return max(0, int(os.getenv("SEEDING_RUNTIME_SNAPSHOT_INTERVAL", "10")))
    except ValueError:
        return 10


async def snapshot_once(pool, session_factory) -> int:
    """Один проход снимка. Возвращает число обновлённых строк."""
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

        await repo.bulk_update_runtime(updates)
        await session.commit()
        return len(updates)


async def runtime_snapshot_loop(pool, session_factory) -> None:
    interval = snapshot_interval()
    if interval <= 0:
        log.info("runtime snapshot loop disabled (interval=0)")
        return
    log.info("runtime snapshot loop started (interval=%ss)", interval)
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                n = await snapshot_once(pool, session_factory)
                if n:
                    log.debug("runtime snapshot: updated %s rows", n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("runtime snapshot failed: %s", exc)
    except asyncio.CancelledError:
        raise
