import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository

from seeding_api.deps import DbSession, EngineClientDep
from seeding_api.schemas import TorrentCreate, TorrentDetailOut, TorrentOut

log = logging.getLogger(__name__)

router = APIRouter()


def _require_engine_for_delete() -> bool:
    """Если True — при ошибке HTTP к движку DELETE возвращает 502, строка в БД не трогается."""
    v = os.getenv("SEEDING_REQUIRE_ENGINE_FOR_DELETE", "").strip().lower()
    return v in ("1", "true", "yes")


@router.get("", response_model=list[TorrentOut])
async def list_torrents(session: DbSession):
    repo = TorrentRepository(session)
    return await repo.list_all()


@router.post("", response_model=TorrentOut, status_code=201)
async def create_torrent(body: TorrentCreate, session: DbSession, engine: EngineClientDep):
    if not body.magnet_uri.startswith("magnet:"):
        raise HTTPException(status_code=422, detail="magnet_uri must start with magnet:")
    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=body.display_name,
        save_path=body.save_path,
        magnet_uri=body.magnet_uri,
    )
    await session.flush()
    await session.refresh(row)
    try:
        await engine.register_torrent(row.id, body.magnet_uri, body.save_path)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(row.id, TorrentStatus.downloading.value)
    await session.refresh(row)
    return row


@router.get("/{torrent_id}", response_model=TorrentDetailOut)
async def get_torrent(torrent_id: int, session: DbSession, engine: EngineClientDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        runtime = await engine.runtime_snapshot(torrent_id)
    except httpx.HTTPError:
        runtime = None
    if runtime and row.info_hash is None:
        ih = runtime.get("info_hash")
        if isinstance(ih, str) and ih and ih != "0" * 40:
            await repo.update_info_hash(torrent_id, ih)
            await session.refresh(row)
    data = TorrentOut.model_validate(row).model_dump()
    data["runtime"] = runtime
    return TorrentDetailOut.model_validate(data)


@router.post("/{torrent_id}/pause", response_model=TorrentOut)
async def pause_torrent(torrent_id: int, session: DbSession, engine: EngineClientDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await engine.pause(torrent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise HTTPException(status_code=502, detail="engine error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(torrent_id, TorrentStatus.paused.value)
    out = await repo.get_by_id(torrent_id)
    assert out is not None
    return out


@router.post("/{torrent_id}/resume", response_model=TorrentOut)
async def resume_torrent(torrent_id: int, session: DbSession, engine: EngineClientDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await engine.resume(torrent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise HTTPException(status_code=502, detail="engine error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(torrent_id, TorrentStatus.downloading.value)
    out = await repo.get_by_id(torrent_id)
    assert out is not None
    return out


@router.delete("/{torrent_id}", status_code=204)
async def delete_torrent(torrent_id: int, session: DbSession, engine: EngineClientDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await engine.remove_from_runtime(torrent_id)
    except httpx.HTTPError as exc:
        if _require_engine_for_delete():
            raise HTTPException(status_code=502, detail="engine unavailable") from exc
        log.warning(
            "delete torrent_id=%s: engine HTTP error, removing DB row anyway; "
            "runtime may be stale until engine restart "
            "(set SEEDING_REQUIRE_ENGINE_FOR_DELETE=1 to require engine and keep row on failure)",
            torrent_id,
            exc_info=True,
        )
    await repo.delete(torrent_id)
