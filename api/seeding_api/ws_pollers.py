"""Адресные WS-пуллеры (Фаза 7, WS-2): фоновый цикл, который опрашивает источники ТОЛЬКО для
каналов, на которые реально есть подписчики, и публикует дельты в хаб.

Зачем отдельный цикл, а не общий `runtime_snapshot_loop`:
- снимок рантайма идёт раз в ~10с (нагрузка на все движки) — для открытой детали это медленно;
- здесь опрашиваем точечно: только открытую деталь (`torrent:{id}`), только открытые настройки
  (`engines`) и только активные джобы (`job:{id}`). Обычно это 0–1 раздача и 0–1 джоба, поэтому
  частый тик (2с) почти бесплатен и не зависит от общего числа раздач.

Всё in-process (один воркер). Многоворкерный fan-out — WS-3 (Redis), отложено.
"""

from __future__ import annotations

import asyncio
import logging

from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime

log = logging.getLogger(__name__)

_BASE_INTERVAL = 2.0  # сек: тик детали и джоб
_ENGINES_EVERY = 3  # каждые N тиков (~6с) пушим health движков


def _ids_from_channels(channels: list[str]) -> list[int]:
    out: list[int] = []
    for ch in channels:
        token = ch.split(":", 1)[1] if ":" in ch else ""
        if token.isdigit():
            out.append(int(token))
    return out


async def _poll_torrents(app, hub) -> None:
    """Для каждой открытой детали (`torrent:{id}`) тянем рантайм её движка и пушим живые поля."""
    ids = _ids_from_channels(hub.channels_with_prefix("torrent:"))
    if not ids:
        return
    factory = app.state.session_factory
    pool = app.state.engine_pool
    async with factory() as session:
        rows = await TorrentRepository(session).get_by_ids(ids)

    async def _one(row) -> None:
        try:
            handle = await pool.client_for(row.engine_id).runtime_snapshot(row.id)
        except Exception:  # noqa: BLE001 — движок недоступен/нет в пуле: отдадим без рантайма
            handle = None
        status = row.status
        if handle is not None and row.status != TorrentStatus.migrating.value:
            status = status_from_runtime(
                handle.get("runtime_status"), handle.get("lt_state"),
                float(handle.get("progress") or 0.0),
            )
        await hub.publish(f"torrent:{row.id}", {"id": row.id, "runtime": handle, "status": status})

    await asyncio.gather(*(_one(r) for r in rows))


async def _poll_jobs(app, hub) -> None:
    """Для каждой отслеживаемой джобы (`job:{id}`) пушим её статус/результат из arq (Redis)."""
    channels = hub.channels_with_prefix("job:")
    if not channels:
        return
    arq = getattr(app.state, "arq_pool", None)
    if arq is None:
        return
    from arq.jobs import Job, JobStatus

    for ch in channels:
        jid = ch.split(":", 1)[1]
        try:
            job = Job(jid, redis=arq)
            status = await job.status()
            out: dict = {"job_id": jid, "status": getattr(status, "value", str(status))}
            if status == JobStatus.complete:
                info = await job.result_info()
                if info is not None:
                    out["success"] = bool(info.success)
                    out["result"] = info.result if info.success else str(info.result)
            await hub.publish(ch, out)
        except Exception as exc:  # noqa: BLE001 — джоба исчезла/redis моргнул: пропустим тик
            log.debug("ws job poll %s failed: %s", ch, exc)


async def ws_pollers_loop(app) -> None:
    hub = getattr(app.state, "ws_hub", None)
    if hub is None:
        return
    log.info("ws pollers loop started (interval=%ss)", _BASE_INTERVAL)
    tick = 0
    try:
        while True:
            await asyncio.sleep(_BASE_INTERVAL)
            tick += 1
            try:
                await _poll_torrents(app, hub)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("ws torrents poll failed: %s", exc)
            try:
                await _poll_jobs(app, hub)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("ws jobs poll failed: %s", exc)
            if tick % _ENGINES_EVERY == 0 and hub.has_subscribers("engines"):
                try:
                    from seeding_api.routers.health import build_health_full

                    await hub.publish("engines", await build_health_full(app))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.debug("ws engines publish failed: %s", exc)
    except asyncio.CancelledError:
        raise
