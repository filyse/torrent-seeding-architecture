"""Перенос раздачи между движками одной машины (Фаза 4).

Стратегия «pull через /media»: целевой движок читает контент исходного движка через общий
read-only mount (`/media`), копирует к себе, перепроверяет хэш и поднимает раздачу. Источник
удаляется только после того, как цель подтвердила полную копию — данные не теряются при сбое.

Перенос идёт фоном (может занять минуты на копировании): пока он идёт, в БД держится статус
`migrating` (см. `merge_runtime_into_row`), а по завершении/ошибке статус возвращается к реальному.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository

log = logging.getLogger(__name__)

_CHECKING_STATES = {
    "checking_files",
    "checking_resume_data",
    "allocating",
    "checking",
    "queued_for_checking",
}


def _verify_timeout() -> int:
    try:
        return max(30, int(os.getenv("SEEDING_MIGRATE_VERIFY_TIMEOUT", "1800")))
    except ValueError:
        return 1800


async def _wait_until_checked(client, db_id: int, timeout: int) -> dict | None:
    """Дождаться окончания recheck на цели и вернуть финальный snapshot (или последний)."""
    interval = 3
    last: dict | None = None
    waited = 0
    while waited <= timeout:
        try:
            snap = await client.runtime_snapshot(db_id)
        except httpx.HTTPError:
            snap = None
        if snap is not None:
            last = snap
            if str(snap.get("lt_state") or "") not in _CHECKING_STATES:
                return snap
        await asyncio.sleep(interval)
        waited += interval
    return last


async def run_migration(
    session_factory,
    pool,
    *,
    torrent_id: int,
    source_engine_id: int | str,
    target_engine_id: str,
    source_save_path: str,
    target_save_path: str,
    src_content_path: str,
    display_name: str,
) -> None:
    """Выполнить перенос. Любой сбой откатывает изменения и не трогает источник."""
    source = pool.client_for(str(source_engine_id))
    target = pool.client_for(target_engine_id)

    async def _set_status(status: str) -> None:
        async with session_factory() as session:
            repo = TorrentRepository(session)
            await repo.update_status(torrent_id, status)
            await session.commit()

    async def _commit_switch() -> None:
        async with session_factory() as session:
            repo = TorrentRepository(session)
            await repo.update_engine(torrent_id, target_engine_id, target_save_path)
            await repo.update_status(torrent_id, TorrentStatus.seeding.value)
            await session.commit()

    try:
        torrent_bytes = await source.get_torrent_file(torrent_id)
        if not torrent_bytes:
            raise RuntimeError("source engine has no .torrent file (magnet-only torrents not supported)")

        # Источник на паузу, но НЕ удаляем — данные нужны для копии и для отката.
        try:
            await source.pause(torrent_id)
        except httpx.HTTPError as exc:
            log.warning("migrate %s: pause source failed (continuing): %s", torrent_id, exc)

        # Копирование контента из /media + add + recheck на целевом движке.
        await target.import_local(torrent_id, torrent_bytes, target_save_path, src_content_path)

        snap = await _wait_until_checked(target, torrent_id, _verify_timeout())
        progress = float(snap.get("progress") or 0.0) if snap else 0.0
        if progress < 0.999:
            # Копия неполная/битая — откат: убрать частичную копию с цели, источник оставить.
            log.error("migrate %s: target incomplete after copy (progress=%.4f), rolling back",
                      torrent_id, progress)
            try:
                await target.remove_from_runtime(
                    torrent_id, delete_files=True,
                    save_path=target_save_path, display_name=display_name,
                )
            except httpx.HTTPError as exc:
                log.warning("migrate %s: cleanup target after failed copy: %s", torrent_id, exc)
            await _resume_source(source, torrent_id)
            await _set_status(TorrentStatus.seeding.value)
            return

        # Цель подтвердила полную копию — переключаем БД и чистим источник.
        await _commit_switch()
        try:
            await source.remove_from_runtime(
                torrent_id, delete_files=True,
                save_path=source_save_path, display_name=display_name,
            )
        except httpx.HTTPError as exc:
            log.warning("migrate %s: source cleanup failed (content may linger): %s", torrent_id, exc)
        log.info("migrate %s: %s -> %s done", torrent_id, source_engine_id, target_engine_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("migrate %s failed: %s", torrent_id, exc)
        await _resume_source(source, torrent_id)
        try:
            await _set_status(TorrentStatus.seeding.value)
        except Exception:  # noqa: BLE001
            log.exception("migrate %s: failed to reset status", torrent_id)


async def _resume_source(source, torrent_id: int) -> None:
    try:
        await source.resume(torrent_id)
    except httpx.HTTPError as exc:
        log.warning("migrate %s: resume source failed: %s", torrent_id, exc)
