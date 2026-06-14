import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx
from arq import cron
from arq.connections import RedisSettings
from seeding_db.config import get_database_url
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime
from seeding_db.session import create_engine, create_session_factory

from seeding_queue.engine_util import check_all_engines_health, engine_url, fetch_all_runtime

log = logging.getLogger(__name__)

_BULK_CONCURRENCY = int(os.getenv("SEEDING_BULK_CONCURRENCY", "16"))


def _redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    u = urlparse(url)
    host = u.hostname or "localhost"
    port = u.port or 6379
    password = u.password
    return RedisSettings(host=host, port=port, password=password)


async def noop_report(ctx):
    """Заглушка: фоновая задача для проверки воркера."""
    return {"ok": True}


async def check_engine_health(ctx):
    """Проверка всех движков из ENGINES_CONFIG / ENGINE_URL."""
    results = await check_all_engines_health()
    log.info("engine health job ok engines=%s", list(results))
    return {"ok": True, "engines": results}


async def sync_runtime_to_db(ctx):
    """Сверка runtime всех движков с БД."""
    runtime_by_engine = await fetch_all_runtime()
    db_url = get_database_url()

    runtime_by_id: dict[int, dict] = {}
    for engine_id, rows in runtime_by_engine.items():
        for row in rows:
            try:
                db_id = int(row.get("db_id"))
            except (TypeError, ValueError):
                continue
            row["_engine_id"] = engine_id
            runtime_by_id[db_id] = row

    eng = create_engine(db_url)
    sf = create_session_factory(eng)
    updated_status = 0
    updated_info_hash = 0
    runtime_missing_db = 0
    db_missing_runtime = 0
    db_total = 0
    runtime_ids_seen: set[int] = set()
    try:
        async with sf() as session:
            repo = TorrentRepository(session)
            db_rows = await repo.list_all()
            db_total = len(db_rows)

            db_by_id = {row.id: row for row in db_rows}
            for db_id, rt in runtime_by_id.items():
                runtime_ids_seen.add(db_id)
                row = db_by_id.get(db_id)
                if row is None:
                    runtime_missing_db += 1
                    continue

                target_status = status_from_runtime(
                    rt.get("runtime_status"),
                    rt.get("lt_state"),
                    rt.get("progress"),
                )
                if row.status != target_status:
                    await repo.update_status(db_id, target_status)
                    updated_status += 1

                rt_info_hash = rt.get("info_hash")
                if isinstance(rt_info_hash, str) and rt_info_hash and row.info_hash != rt_info_hash:
                    await repo.update_info_hash(db_id, rt_info_hash)
                    updated_info_hash += 1

            for row in db_rows:
                if row.id not in runtime_ids_seen and row.status in (
                    TorrentStatus.downloading.value,
                    TorrentStatus.seeding.value,
                ):
                    db_missing_runtime += 1

            await session.commit()
    finally:
        await eng.dispose()

    result = {
        "ok": True,
        "engines": {eid: len(rows) for eid, rows in runtime_by_engine.items()},
        "runtime_total": sum(len(v) for v in runtime_by_engine.values()),
        "db_total": db_total,
        "updated_status": updated_status,
        "updated_info_hash": updated_info_hash,
        "runtime_missing_db": runtime_missing_db,
        "db_missing_runtime": db_missing_runtime,
    }
    log.info("sync_runtime_to_db done %s", result)
    return result


async def _register_one(client: httpx.AsyncClient, base: str, row) -> bool:
    body: dict = {"db_id": row.id, "save_path": row.save_path}
    if row.magnet_uri:
        body["magnet_uri"] = row.magnet_uri
    r = await client.post(f"{base}/internal/v1/torrents", json=body)
    return r.status_code < 300


async def bulk_register_engine(ctx, engine_id: str):
    """Регистрация всех queued торрентов одного движка (bulk через очередь)."""
    base = engine_url(engine_id).rstrip("/")
    db_url = get_database_url()
    eng = create_engine(db_url)
    sf = create_session_factory(eng)
    registered = 0
    failed = 0
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)
    try:
        async with sf() as session:
            repo = TorrentRepository(session)
            rows = await repo.list_queued_for_engine(engine_id)
            if not rows:
                return {"ok": True, "engine_id": engine_id, "registered": 0, "failed": 0}

            async with httpx.AsyncClient(timeout=30.0) as client:
                async def one(row) -> None:
                    nonlocal registered, failed
                    async with sem:
                        try:
                            ok = await _register_one(client, base, row)
                            if ok:
                                await repo.update_status(row.id, TorrentStatus.downloading.value)
                                registered += 1
                            else:
                                failed += 1
                        except httpx.HTTPError:
                            failed += 1

                await asyncio.gather(*[one(row) for row in rows])
            await session.commit()
    finally:
        await eng.dispose()

    log.info("bulk_register_engine %s registered=%s failed=%s", engine_id, registered, failed)
    return {"ok": True, "engine_id": engine_id, "registered": registered, "failed": failed}


async def restore_engine(ctx, engine_id: str):
    """
    Фоновое восстановление активных торрентов одного движка.
    Дублирует логику API restore, но через очередь (bulk).
    """
    base = engine_url(engine_id).rstrip("/")
    db_url = get_database_url()
    eng = create_engine(db_url)
    sf = create_session_factory(eng)
    restored = 0
    failed = 0
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            hr = await client.get(f"{base}/health")
            hr.raise_for_status()

            async with sf() as session:
                repo = TorrentRepository(session)
                rows = [r for r in await repo.list_by_engine(engine_id) if r.status in (
                    TorrentStatus.downloading.value,
                    TorrentStatus.seeding.value,
                    TorrentStatus.paused.value,
                )]

                async def one(row) -> None:
                    nonlocal restored, failed
                    async with sem:
                        try:
                            snap_r = await client.get(f"{base}/internal/v1/torrents/{row.id}")
                            if snap_r.status_code == 404:
                                if row.magnet_uri:
                                    reg = await client.post(
                                        f"{base}/internal/v1/torrents",
                                        json={
                                            "db_id": row.id,
                                            "magnet_uri": row.magnet_uri,
                                            "save_path": row.save_path,
                                        },
                                    )
                                    reg.raise_for_status()
                                else:
                                    reg = await client.post(
                                        f"{base}/internal/v1/torrents/{row.id}/restore-from-disk",
                                        params={"save_path": row.save_path},
                                    )
                                    if reg.status_code == 404:
                                        failed += 1
                                        return
                                    reg.raise_for_status()
                            if row.status == TorrentStatus.paused.value:
                                await client.post(f"{base}/internal/v1/torrents/{row.id}/pause")
                            restored += 1
                        except httpx.HTTPError:
                            failed += 1

                await asyncio.gather(*[one(row) for row in rows])
    finally:
        await eng.dispose()

    log.info("restore_engine %s restored=%s failed=%s", engine_id, restored, failed)
    return {"ok": True, "engine_id": engine_id, "restored": restored, "failed": failed}


async def restore_all_engines(ctx):
    from seeding_db.engine_registry import load_engine_specs

    specs = load_engine_specs()
    results = await asyncio.gather(*[restore_engine(ctx, s.id) for s in specs])
    return {"ok": True, "engines": list(results)}


class WorkerSettings:
    functions = [
        noop_report,
        check_engine_health,
        sync_runtime_to_db,
        bulk_register_engine,
        restore_engine,
        restore_all_engines,
    ]
    cron_jobs = [cron(sync_runtime_to_db, minute=set(range(0, 60, 2)), run_at_startup=True)]
    redis_settings = _redis_settings()
    max_jobs = 8
