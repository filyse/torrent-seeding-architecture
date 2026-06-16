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
import time

import httpx
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository

log = logging.getLogger(__name__)


def set_progress(
    store: dict,
    torrent_id: int,
    phase: str,
    *,
    progress: float | None = None,
    copied: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> None:
    """Записать снимок прогресса переноса для опроса из UI."""
    if store is None:
        return
    store[torrent_id] = {
        "phase": phase,
        "progress": progress,
        "copied": copied,
        "total": total,
        "message": message,
        "updated_at": time.time(),
    }

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


async def _wait_until_checked(
    client, db_id: int, timeout: int, store: dict | None = None
) -> dict | None:
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
            if store is not None:
                set_progress(
                    store, db_id, "checking",
                    progress=float(snap.get("progress") or 0.0),
                )
            if str(snap.get("lt_state") or "") not in _CHECKING_STATES:
                return snap
        await asyncio.sleep(interval)
        waited += interval
    return last


async def _run_import_with_progress(
    target, store: dict | None, torrent_id: int,
    torrent_bytes: bytes, target_save_path: str, src_content_path: str,
) -> None:
    """Запустить import_local и параллельно опрашивать прогресс копирования у движка."""
    task = asyncio.create_task(
        target.import_local(torrent_id, torrent_bytes, target_save_path, src_content_path)
    )
    while not task.done():
        try:
            prog = await target.get_migrate_progress(torrent_id)
        except httpx.HTTPError:
            prog = None
        if prog and prog.get("active") and store is not None:
            copied = int(prog.get("copied") or 0)
            total = int(prog.get("total") or 0)
            phase = str(prog.get("phase") or "copying")
            pct = (copied / total) if total > 0 else None
            set_progress(store, torrent_id, phase, progress=pct, copied=copied, total=total)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception:  # noqa: BLE001 — пробросим ниже через await task
            break
    await task


async def _http_transfer(
    source, target, store: dict | None, torrent_id: int,
    torrent_bytes: bytes, target_save_path: str,
) -> None:
    """Сетевой перенос между машинами: оркестратор стримит контент из источника tar-потоком
    и проксирует его в приёмник, попутно считая прогресс по переданным байтам."""
    async with source.stream_content(torrent_id) as (resp, total):
        await target.stage_remote(torrent_id, torrent_bytes, target_save_path, total)
        copied = 0

        async def gen():
            nonlocal copied
            async for chunk in resp.aiter_bytes():
                copied += len(chunk)
                pct = (copied / total) if total > 0 else None
                set_progress(store, torrent_id, "copying", progress=pct, copied=copied, total=total)
                yield chunk

        await target.import_remote(torrent_id, gen())


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
    transport: str = "media",
    progress_store: dict | None = None,
) -> None:
    """Выполнить перенос. Любой сбой откатывает изменения и не трогает источник."""
    source = pool.client_for(str(source_engine_id))
    target = pool.client_for(target_engine_id)
    store = progress_store

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
        set_progress(store, torrent_id, "preparing", message=f"→ {target_engine_id}")
        torrent_bytes = await source.get_torrent_file(torrent_id)
        if not torrent_bytes:
            raise RuntimeError("source engine has no .torrent file (magnet-only torrents not supported)")

        # Источник на паузу, но НЕ удаляем — данные нужны для копии и для отката.
        try:
            await source.pause(torrent_id)
        except httpx.HTTPError as exc:
            log.warning("migrate %s: pause source failed (continuing): %s", torrent_id, exc)

        # Копирование контента + add + recheck на целевом движке.
        set_progress(store, torrent_id, "copying", progress=0.0)
        if transport == "http":
            # Между машинами: контент идёт по сети через оркестратор (общего /media нет).
            await _http_transfer(source, target, store, torrent_id, torrent_bytes, target_save_path)
        else:
            # На одной машине: приёмник копирует контент из общего read-only /media.
            await _run_import_with_progress(
                target, store, torrent_id, torrent_bytes, target_save_path, src_content_path
            )

        snap = await _wait_until_checked(target, torrent_id, _verify_timeout(), store)
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
            set_progress(store, torrent_id, "error",
                         message=f"копия неполная (progress={progress:.2%})")
            return

        # Цель подтвердила полную копию — переключаем БД и чистим источник.
        set_progress(store, torrent_id, "finalizing", progress=1.0)
        await _commit_switch()
        try:
            await source.remove_from_runtime(
                torrent_id, delete_files=True,
                save_path=source_save_path, display_name=display_name,
            )
        except httpx.HTTPError as exc:
            log.warning("migrate %s: source cleanup failed (content may linger): %s", torrent_id, exc)
        set_progress(store, torrent_id, "done", progress=1.0,
                     message=f"{source_engine_id} → {target_engine_id}")
        log.info("migrate %s: %s -> %s done", torrent_id, source_engine_id, target_engine_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("migrate %s failed: %s", torrent_id, exc)
        await _resume_source(source, torrent_id)
        try:
            await _set_status(TorrentStatus.seeding.value)
        except Exception:  # noqa: BLE001
            log.exception("migrate %s: failed to reset status", torrent_id)
        set_progress(store, torrent_id, "error", message=str(exc)[:200])


async def _resume_source(source, torrent_id: int) -> None:
    try:
        await source.resume(torrent_id)
    except httpx.HTTPError as exc:
        log.warning("migrate %s: resume source failed: %s", torrent_id, exc)
