"""Перенос раздачи между движками (Фаза 4) — возобновляемый.

Стратегии передачи контента:
  • `media` — на одной машине: приёмник копирует контент источника через общий read-only
    mount (`/media`). Копирование инкрементальное (по файлам), поэтому повтор после сбоя
    докопирует только недостающее.
  • `http` — между машинами: оркестратор тянет контент пофайлово (с Range) и проксирует в
    приёмник; уже принятые файлы/байты пропускаются. Повтор продолжает с места обрыва.

Источник всё время остаётся нетронутым (только на паузе) и удаляется лишь после того, как
цель подтвердила полную копию. При сбое частичная копия на цели НЕ удаляется — состояние
переноса фиксируется в БД (`migration_jobs`, state=failed), и перенос можно возобновить;
полная отмена (с удалением частичной копии) — отдельным действием.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
from seeding_db.models import TorrentStatus
from seeding_db.repository import MigrationRepository, TorrentRepository

log = logging.getLogger(__name__)

# Не чаще раза в N секунд пишем прогресс в БД (в память — каждый чанк).
_DB_PROGRESS_INTERVAL = 3.0


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
    """Записать снимок прогресса переноса для опроса из UI (быстрый in-memory store)."""
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
    on_progress,
) -> None:
    """media-перенос: import_local (инкрементальная копия) + опрос прогресса у движка."""
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
            await on_progress(phase, copied, total)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception:  # noqa: BLE001 — пробросим ниже через await task
            break
    await task


async def _http_transfer_resumable(
    source, target, store: dict | None, torrent_id: int,
    torrent_bytes: bytes, target_save_path: str, on_progress,
) -> None:
    """Сетевой перенос пофайлово с докачкой: оркестратор тянет каждый файл с источника
    (с Range от уже принятого размера) и дописывает его на приёмнике."""
    manifest = await source.content_manifest(torrent_id)
    if not manifest:
        raise RuntimeError("source content manifest unavailable")
    root = str(manifest.get("root") or "")
    files = manifest.get("files") or []
    total = int(manifest.get("total") or sum(int(f.get("size") or 0) for f in files))
    await target.stage_remote(torrent_id, torrent_bytes, target_save_path, total)

    copied = 0
    for f in files:
        rel = str(f.get("path") or "")
        size = int(f.get("size") or 0)
        if not rel:
            continue
        have = min(await target.import_file_size(torrent_id, target_save_path, root, rel), size)
        if size > 0 and have >= size:
            copied += size
            pct = (copied / total) if total > 0 else None
            set_progress(store, torrent_id, "copying", progress=pct, copied=copied, total=total)
            await on_progress("copying", copied, total)
            continue

        copied += have  # уже принятая часть файла
        async with source.stream_content_file(torrent_id, rel, have) as resp:
            async def gen():
                nonlocal copied
                async for chunk in resp.aiter_bytes():
                    copied += len(chunk)
                    pct = (copied / total) if total > 0 else None
                    set_progress(store, torrent_id, "copying", progress=pct, copied=copied, total=total)
                    await on_progress("copying", copied, total)
                    yield chunk

            await target.import_file_append(torrent_id, target_save_path, root, rel, have, gen())

    set_progress(store, torrent_id, "checking", progress=None, copied=copied, total=total)
    await on_progress("checking", copied, total)
    await target.import_finalize(torrent_id)


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
    resume: bool = False,
) -> None:
    """Выполнить (или возобновить) перенос. При сбое частичная копия сохраняется для повтора."""
    source = pool.client_for(str(source_engine_id))
    target = pool.client_for(target_engine_id)
    store = progress_store
    last_db_write = 0.0

    async def _set_status(status: str) -> None:
        async with session_factory() as session:
            repo = TorrentRepository(session)
            await repo.update_status(torrent_id, status)
            await session.commit()

    async def _job(**fields) -> None:
        async with session_factory() as session:
            await MigrationRepository(session).upsert(torrent_id, **fields)
            await session.commit()

    async def _job_state(state: str, *, phase: str | None = None, error: str | None = None) -> None:
        async with session_factory() as session:
            await MigrationRepository(session).set_state(torrent_id, state, phase=phase, error=error)
            await session.commit()

    async def _on_progress(phase: str, copied: int, total: int) -> None:
        nonlocal last_db_write
        now = time.monotonic()
        if now - last_db_write < _DB_PROGRESS_INTERVAL:
            return
        last_db_write = now
        async with session_factory() as session:
            await MigrationRepository(session).set_progress(
                torrent_id, phase=phase, copied=copied, total=total
            )
            await session.commit()

    async def _commit_switch() -> None:
        async with session_factory() as session:
            repo = TorrentRepository(session)
            await repo.update_engine(torrent_id, target_engine_id, target_save_path)
            await repo.update_status(torrent_id, TorrentStatus.seeding.value)
            await session.commit()

    await _job(
        source_engine_id=str(source_engine_id),
        target_engine_id=target_engine_id,
        source_save_path=source_save_path,
        target_save_path=target_save_path,
        src_content_path=src_content_path,
        display_name=display_name,
        transport=transport,
        state="running",
        phase="preparing",
        last_error="",
    )
    async with session_factory() as session:
        await MigrationRepository(session).bump_attempts(torrent_id)
        await session.commit()

    try:
        verb = "resume" if resume else "start"
        set_progress(store, torrent_id, "preparing", message=f"{verb} → {target_engine_id}")
        torrent_bytes = await source.get_torrent_file(torrent_id)
        if not torrent_bytes:
            raise RuntimeError("source engine has no .torrent file (magnet-only torrents not supported)")

        # Источник на паузу, но НЕ удаляем — данные нужны для копии и для возобновления.
        try:
            await source.pause(torrent_id)
        except httpx.HTTPError as exc:
            log.warning("migrate %s: pause source failed (continuing): %s", torrent_id, exc)

        set_progress(store, torrent_id, "copying", progress=0.0)
        await _job_state("running", phase="copying")
        if transport == "http":
            await _http_transfer_resumable(
                source, target, store, torrent_id, torrent_bytes, target_save_path, _on_progress
            )
        else:
            await _run_import_with_progress(
                target, store, torrent_id, torrent_bytes, target_save_path, src_content_path,
                _on_progress,
            )

        snap = await _wait_until_checked(target, torrent_id, _verify_timeout(), store)
        progress = float(snap.get("progress") or 0.0) if snap else 0.0
        if progress < 0.999:
            # Копия неполная — НЕ удаляем частичные данные: оставляем для возобновления.
            log.error("migrate %s: target incomplete (progress=%.4f), keeping partial for resume",
                      torrent_id, progress)
            await _resume_source(source, torrent_id)
            await _set_status(TorrentStatus.seeding.value)
            await _job_state(
                "failed", phase="error",
                error=f"копия неполная (progress={progress:.2%}) — можно возобновить",
            )
            set_progress(store, torrent_id, "error", progress=progress,
                         message=f"копия неполная ({progress:.2%}) — возобновляемо")
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
        async with session_factory() as session:
            await MigrationRepository(session).delete(torrent_id)
            await session.commit()
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
        try:
            await _job_state("failed", phase="error", error=str(exc)[:400])
        except Exception:  # noqa: BLE001
            log.exception("migrate %s: failed to persist job error", torrent_id)
        set_progress(store, torrent_id, "error",
                     message=f"{str(exc)[:180]} — возобновляемо")


async def cancel_migration(
    session_factory,
    pool,
    *,
    torrent_id: int,
) -> bool:
    """Полная отмена переноса: удалить частичную копию на цели, вернуть источник в работу."""
    async with session_factory() as session:
        job = await MigrationRepository(session).get(torrent_id)
        if job is None:
            return False
        target_engine_id = job.target_engine_id
        target_save_path = job.target_save_path
        source_engine_id = job.source_engine_id
        display_name = job.display_name

    target = pool.client_for(target_engine_id)
    try:
        await target.remove_from_runtime(
            torrent_id, delete_files=True,
            save_path=target_save_path, display_name=display_name,
        )
    except httpx.HTTPError as exc:
        log.warning("cancel migrate %s: target cleanup failed: %s", torrent_id, exc)

    await _resume_source(pool.client_for(str(source_engine_id)), torrent_id)
    async with session_factory() as session:
        repo = TorrentRepository(session)
        await repo.update_status(torrent_id, TorrentStatus.seeding.value)
        await MigrationRepository(session).delete(torrent_id)
        await session.commit()
    return True


async def _resume_source(source, torrent_id: int) -> None:
    try:
        await source.resume(torrent_id)
    except httpx.HTTPError as exc:
        log.warning("migrate %s: resume source failed: %s", torrent_id, exc)
