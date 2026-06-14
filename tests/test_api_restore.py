import asyncio
import importlib
import json
import re
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from seeding_api.engine_pool import EnginePool
from seeding_api.restore import maybe_restore_torrents_to_engine
from seeding_db.engine_registry import EngineSpec
from seeding_db.models import Base, TorrentStatus
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ENGINE = "http://engine.test:8081"


def _handle(db_id: int, magnet: str, sp: str, rs: str) -> dict:
    return {
        "db_id": db_id,
        "magnet_uri": magnet,
        "save_path": sp,
        "runtime_status": rs,
        "info_hash": None,
        "progress": None,
        "lt_state": None,
    }


@pytest.fixture
async def restore_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_restore_registers_and_pauses_paused_row(restore_factory):
    async with restore_factory() as s:
        repo = TorrentRepository(s)
        m = "magnet:?xt=urn:btih:dddddddddddddddddddddddddddddddddddddddd"
        await repo.create(
            display_name="a",
            save_path="/data",
            magnet_uri=m,
            status=TorrentStatus.downloading.value,
        )
        await repo.create(
            display_name="b",
            save_path="/data",
            magnet_uri=m,
            status=TorrentStatus.paused.value,
        )
        await s.commit()

    ec = AsyncMock()
    ec.health = AsyncMock(return_value={"status": "ok"})
    ec.runtime_snapshot = AsyncMock(return_value=None)
    ec.register_torrent = AsyncMock(
        side_effect=lambda db_id, mag, sp: _handle(db_id, mag, sp, "active"),
    )
    ec.pause = AsyncMock(
        side_effect=lambda db_id: _handle(db_id, m, "/data", "paused"),
    )
    ec.resume = AsyncMock()

    pool = EnginePool([EngineSpec(id="default", url=ENGINE, storage_prefix="/data")])
    pool._by_id["default"] = ec  # type: ignore[method-assign]
    await maybe_restore_torrents_to_engine(restore_factory, pool)

    assert ec.register_torrent.await_count == 2
    ec.pause.assert_awaited_once()
    ec.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_skipped_when_disabled(restore_factory, monkeypatch):
    monkeypatch.setenv("SEEDING_ENGINE_RESTORE", "0")
    ec = AsyncMock()
    ec.health = AsyncMock()
    pool = EnginePool([EngineSpec(id="default", url=ENGINE, storage_prefix="/data")])
    pool._by_id["default"] = ec  # type: ignore[method-assign]
    await maybe_restore_torrents_to_engine(restore_factory, pool)
    ec.health.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_skipped_when_engine_down(restore_factory):
    ec = AsyncMock()
    ec.health = AsyncMock(side_effect=httpx.ConnectError("nop", request=None))

    pool = EnginePool([EngineSpec(id="default", url=ENGINE, storage_prefix="/data")])
    pool._by_id["default"] = ec  # type: ignore[method-assign]
    await maybe_restore_torrents_to_engine(restore_factory, pool)
    ec.runtime_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_sync_pause_when_already_in_engine(restore_factory):
    async with restore_factory() as s:
        repo = TorrentRepository(s)
        m = "magnet:?xt=urn:btih:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        row = await repo.create(
            display_name="z",
            save_path="/data",
            magnet_uri=m,
            status=TorrentStatus.paused.value,
        )
        await s.commit()
        rid = row.id

    snap_active = _handle(rid, m, "/data", "active")
    ec = AsyncMock()
    ec.health = AsyncMock(return_value={})
    ec.runtime_snapshot = AsyncMock(return_value=snap_active)
    ec.register_torrent = AsyncMock()
    ec.pause = AsyncMock(return_value=_handle(rid, m, "/data", "paused"))
    ec.resume = AsyncMock()

    pool = EnginePool([EngineSpec(id="default", url=ENGINE, storage_prefix="/data")])
    pool._by_id["default"] = ec  # type: ignore[method-assign]
    await maybe_restore_torrents_to_engine(restore_factory, pool)

    ec.register_torrent.assert_not_awaited()
    ec.pause.assert_awaited_once_with(rid)
    ec.resume.assert_not_awaited()


def test_api_lifespan_runs_restore_for_seeded_db(monkeypatch, tmp_path):
    db = tmp_path / "life.db"
    url = f"sqlite+aiosqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)

    async def seed():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with fac() as s:
            repo = TorrentRepository(s)
            await repo.create(
                display_name="x",
                save_path="/data",
                magnet_uri="magnet:?xt=urn:btih:ffffffffffffffffffffffffffffffffffffffff",
                status=TorrentStatus.paused.value,
            )
            await s.commit()
        await eng.dispose()

    asyncio.run(seed())

    import seeding_api.main as main

    importlib.reload(main)

    registered: list[int] = []
    pauses: list[int] = []

    def on_reg(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        registered.append(body["db_id"])
        return httpx.Response(
            200,
            json=_handle(body["db_id"], body["magnet_uri"], body["save_path"], "active"),
        )

    def on_pause(request: httpx.Request) -> httpx.Response:
        tid = int(request.url.path.rstrip("/").split("/")[-2])
        pauses.append(tid)
        return httpx.Response(200, json=_handle(tid, "magnet:x", "/data", "paused"))

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ENGINE}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok", "service": "engine"}),
        )
        mock.get(url__regex=re.compile(re.escape(ENGINE) + r"/internal/v1/torrents/\d+$")).mock(
            return_value=httpx.Response(404, json={"detail": "torrent not in engine runtime"}),
        )
        mock.post(f"{ENGINE}/internal/v1/torrents").mock(side_effect=on_reg)
        mock.post(url__regex=re.compile(re.escape(ENGINE) + r"/internal/v1/torrents/\d+/pause$")).mock(
            side_effect=on_pause,
        )

        with TestClient(main.app) as client:
            assert client.get("/api/v1/health").status_code == 200

    assert registered == [1]
    assert pauses == [1]
