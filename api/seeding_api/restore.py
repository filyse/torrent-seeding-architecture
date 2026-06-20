"""После перезапуска API: поднять торренты в движках по строкам БД (параллельно по engine_id)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict

import httpx
from seeding_db.models import TorrentRecord, TorrentStatus
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from seeding_api.engine_client import EngineClient
from seeding_api.engine_pool import EnginePool

log = logging.getLogger(__name__)

_RESTORE_CONCURRENCY = int(os.getenv("SEEDING_RESTORE_CONCURRENCY", "32"))


def _is_complete_seed_snap(snap: dict) -> bool:
    progress = snap.get("progress")
    lt_state = (snap.get("lt_state") or "").strip().lower()
    if lt_state in {"seeding", "finished"}:
        return True
    if progress is not None and float(progress) >= 0.999:
        return lt_state not in {"downloading", "downloading_metadata"}
    return False


async def _sync_pause_from_db(db_id: int, db_status: str, ec: EngineClient, snap: dict) -> None:
    # Готовые сиды после restore всегда активны (не оставляем в паузе из stale БД)
    if _is_complete_seed_snap(snap):
        if snap.get("runtime_status") == "paused":
            try:
                await ec.resume(db_id)
            except httpx.HTTPError as exc:
                log.warning("restore auto-resume seed id=%s failed: %s", db_id, exc)
        return
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


async def _restore_magnet_row(
    db_id: int,
    magnet_uri: str,
    save_path: str,
    status: str,
    ec: EngineClient,
    sem: asyncio.Semaphore,
) -> None:
    if not magnet_uri.startswith("magnet:"):
        log.warning("restore skip id=%s: invalid magnet_uri", db_id)
        return
    async with sem:
        try:
            snap = await ec.runtime_snapshot(db_id)
        except httpx.HTTPError as exc:
            log.warning("restore snapshot id=%s failed: %s", db_id, exc)
            return
        if snap is None:
            try:
                snap = await ec.register_torrent(db_id, magnet_uri, save_path)
            except httpx.HTTPError as exc:
                log.warning("restore register id=%s failed: %s", db_id, exc)
                return
        await _sync_pause_from_db(db_id, status, ec, snap)


async def _restore_file_row(
    db_id: int,
    save_path: str,
    status: str,
    ec: EngineClient,
    sem: asyncio.Semaphore,
) -> None:
    async with sem:
        try:
            snap = await ec.runtime_snapshot(db_id)
        except httpx.HTTPError as exc:
            log.warning("restore file snapshot id=%s failed: %s", db_id, exc)
            return
        if snap is None:
            try:
                snap = await ec.restore_from_disk(db_id, save_path)
            except httpx.HTTPError as exc:
                log.warning("restore file id=%s failed: %s", db_id, exc)
                return
            if snap is None:
                log.warning("restore file skip id=%s: no .torrent on engine disk", db_id)
                return
        await _sync_pause_from_db(db_id, status, ec, snap)


async def restore_rows_for_engine(
    pool: EnginePool,
    engine_id: str,
    magnet_rows: list[tuple[int, str, str, str]],
    file_rows: list[tuple[int, str, str]],
) -> None:
    # Движок мог выпасть из пула (устаревший heartbeat / ещё не зарегистрировался после
    # рестарта). Тогда client_for бросает KeyError — ловим и пропускаем, чтобы не ронять
    # старт API. Раздачи восстановятся, когда движок вернётся в пул и API рестартует.
    try:
        ec = pool.client_for(engine_id)
    except KeyError:
        log.warning(
            "engine %s not in pool (stale/unregistered), skip restore of %s row(s)",
            engine_id,
            len(magnet_rows) + len(file_rows),
        )
        return

    try:
        await ec.health()
    except httpx.HTTPError:
        log.warning("engine %s unavailable, skip restore", engine_id)
        return

    sem = asyncio.Semaphore(_RESTORE_CONCURRENCY)
    tasks = [
        _restore_magnet_row(db_id, magnet, sp, st, ec, sem)
        for db_id, magnet, sp, st in magnet_rows
    ]
    tasks += [
        _restore_file_row(db_id, sp, st, ec, sem)
        for db_id, sp, st in file_rows
    ]
    if tasks:
        await asyncio.gather(*tasks)
        log.info(
            "engine %s restore: %s magnet + %s file row(s)",
            engine_id,
            len(magnet_rows),
            len(file_rows),
        )


def _group_magnet(rows: list[TorrentRecord]) -> dict[str, list[tuple[int, str, str, str]]]:
    grouped: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for r in rows:
        grouped[r.engine_id].append((r.id, r.magnet_uri or "", r.save_path, r.status))
    return grouped


def _group_file(rows: list[TorrentRecord]) -> dict[str, list[tuple[int, str, str]]]:
    grouped: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for r in rows:
        grouped[r.engine_id].append((r.id, r.save_path, r.status))
    return grouped


async def maybe_restore_torrents_to_engine(
    session_factory: async_sessionmaker[AsyncSession],
    pool: EnginePool,
) -> None:
    if os.getenv("SEEDING_ENGINE_RESTORE", "1").lower() in ("0", "false", "no"):
        return

    async with session_factory() as session:
        repo = TorrentRepository(session)
        magnet_rows = await repo.list_for_engine_restore()
        file_rows = await repo.list_for_torrent_file_restore()

    magnet_by_engine = _group_magnet(magnet_rows)
    file_by_engine = _group_file(file_rows)
    engine_ids = set(magnet_by_engine) | set(file_by_engine)

    # return_exceptions=True: рестор одного движка не должен ронять старт API целиком.
    results = await asyncio.gather(
        *[
            restore_rows_for_engine(
                pool,
                eid,
                magnet_by_engine.get(eid, []),
                file_by_engine.get(eid, []),
            )
            for eid in sorted(engine_ids)
        ],
        return_exceptions=True,
    )
    for eid, res in zip(sorted(engine_ids), results):
        if isinstance(res, Exception):
            log.error("engine %s restore failed (isolated): %r", eid, res)
