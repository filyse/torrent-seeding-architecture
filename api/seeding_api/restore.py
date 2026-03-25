"""После перезапуска API: поднять торренты в движке по строкам БД (оркестрация)."""

from __future__ import annotations

import logging
import os

import httpx
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from seeding_api.engine_client import EngineClient

log = logging.getLogger(__name__)


async def _sync_pause_from_db(db_id: int, db_status: str, ec: EngineClient, snap: dict) -> None:
    want_pause = db_status == TorrentStatus.paused.value
    is_paused = snap.get("runtime_status") == "paused"
    if want_pause == is_paused:
        return
    try:
        if want_pause:
            await ec.pause(db_id)
        else:
            await ec.resume(db_id)
    except httpx.HTTPError as exc:
        log.warning("restore sync pause id=%s failed: %s", db_id, exc)


async def maybe_restore_torrents_to_engine(
    session_factory: async_sessionmaker[AsyncSession],
    ec: EngineClient,
) -> None:
    if os.getenv("SEEDING_ENGINE_RESTORE", "1").lower() in ("0", "false", "no"):
        return
    try:
        await ec.health()
    except httpx.HTTPError:
        log.warning("engine unavailable, skip torrent restore")
        return

    async with session_factory() as session:
        repo = TorrentRepository(session)
        rows = await repo.list_for_engine_restore()
        todo = [(r.id, r.magnet_uri or "", r.save_path, r.status) for r in rows]

    for db_id, magnet_uri, save_path, status in todo:
        if not magnet_uri.startswith("magnet:"):
            log.warning("restore skip id=%s: invalid magnet_uri", db_id)
            continue
        try:
            snap = await ec.runtime_snapshot(db_id)
        except httpx.HTTPError as exc:
            log.warning("restore snapshot id=%s failed: %s", db_id, exc)
            continue
        if snap is None:
            try:
                snap = await ec.register_torrent(db_id, magnet_uri, save_path)
            except httpx.HTTPError as exc:
                log.warning("restore register id=%s failed: %s", db_id, exc)
                continue
        await _sync_pause_from_db(db_id, status, ec, snap)

    if todo:
        log.info("engine restore: processed %s torrent row(s)", len(todo))
