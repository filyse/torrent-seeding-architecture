"""Сборка начальных снапшотов для каналов WebSocket (Фаза 7).

На ``subscribe`` отдаём клиенту текущее состояние сразу (чтобы не ждать первой дельты от
фонового источника): ``stats`` (агрегаты сессии), ``engines`` (health компонентов),
``torrent:{id}`` (рантайм раздачи), ``job:{id}`` (статус/результат задачи). Для ``migrate:{id}``
снапшот не нужен — первый пуш прилетает от ``set_progress`` почти сразу.
"""

from __future__ import annotations

import logging
from typing import Any

from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime

log = logging.getLogger(__name__)


async def _stats_snapshot(app: Any) -> dict:
    pool = app.state.engine_pool
    by_engine = await pool.session_stats_all()
    return {"stats": pool.aggregate_session_stats(by_engine)}


async def _engines_snapshot(app: Any) -> dict:
    from seeding_api.routers.health import build_health_full

    return await build_health_full(app)


async def _torrent_snapshot(app: Any, torrent_id: int) -> dict | None:
    factory = app.state.session_factory
    pool = app.state.engine_pool
    async with factory() as session:
        row = await TorrentRepository(session).get_by_id(torrent_id)
    if row is None:
        return None
    try:
        handle = await pool.client_for(row.engine_id).runtime_snapshot(row.id)
    except Exception:  # noqa: BLE001
        handle = None
    status = row.status
    if handle is not None and row.status != TorrentStatus.migrating.value:
        status = status_from_runtime(
            handle.get("runtime_status"), handle.get("lt_state"),
            float(handle.get("progress") or 0.0),
        )
    return {"id": row.id, "runtime": handle, "status": status}


async def _job_snapshot(app: Any, job_id: str) -> dict | None:
    arq = getattr(app.state, "arq_pool", None)
    if arq is None:
        return None
    from arq.jobs import Job, JobStatus

    job = Job(job_id, redis=arq)
    status = await job.status()
    out: dict = {"job_id": job_id, "status": getattr(status, "value", str(status))}
    if status == JobStatus.complete:
        info = await job.result_info()
        if info is not None:
            out["success"] = bool(info.success)
            out["result"] = info.result if info.success else str(info.result)
    return out


async def initial_snapshot(app: Any, channel: str) -> Any | None:
    """Вернуть начальный снапшот для канала или None, если сборка не поддержана."""
    if channel == "stats":
        return await _stats_snapshot(app)
    if channel == "engines":
        return await _engines_snapshot(app)
    if channel.startswith("torrent:"):
        token = channel.split(":", 1)[1]
        if token.isdigit():
            return await _torrent_snapshot(app, int(token))
    if channel.startswith("job:"):
        return await _job_snapshot(app, channel.split(":", 1)[1])
    return None
