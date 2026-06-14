import httpx
import pytest
import respx
from seeding_db.models import Base, TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.session import create_engine, create_session_factory
from seeding_queue.worker import sync_runtime_to_db


@pytest.mark.asyncio
async def test_sync_runtime_to_db_updates_status_and_infohash(monkeypatch, tmp_path):
    db_path = tmp_path / "sync.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine_url = "http://engine.sync.test:8081"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ENGINE_URL", engine_url)

    eng = create_engine(db_url)
    sf = create_session_factory(eng)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as session:
            repo = TorrentRepository(session)
            row = await repo.create(
                display_name="sync-me",
                save_path="/data",
                magnet_uri="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                status=TorrentStatus.downloading.value,
            )
            await session.commit()
            row_id = row.id

        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{engine_url}/internal/v1/torrents").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "db_id": row_id,
                            "runtime_status": "paused",
                            "lt_state": "downloading",
                            "info_hash": "f" * 40,
                        }
                    ],
                )
            )
            result = await sync_runtime_to_db({})

        assert result["ok"] is True
        assert result["runtime_total"] == 1
        assert result["updated_status"] == 1
        assert result["updated_info_hash"] == 1
        assert result["runtime_missing_db"] == 0
        assert result["db_missing_runtime"] == 0

        async with sf() as session:
            repo = TorrentRepository(session)
            got = await repo.get_by_id(row_id)
            assert got is not None
            assert got.status == TorrentStatus.paused.value
            assert got.info_hash == "f" * 40
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_sync_runtime_to_db_detects_db_missing_runtime(monkeypatch, tmp_path):
    db_path = tmp_path / "sync-missing.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine_url = "http://engine.sync.test:8081"

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ENGINE_URL", engine_url)

    eng = create_engine(db_url)
    sf = create_session_factory(eng)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with sf() as session:
            repo = TorrentRepository(session)
            await repo.create(
                display_name="active-no-runtime",
                save_path="/data",
                magnet_uri="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                status=TorrentStatus.downloading.value,
            )
            await session.commit()

        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"{engine_url}/internal/v1/torrents").mock(
                return_value=httpx.Response(200, json=[])
            )
            result = await sync_runtime_to_db({})

        assert result["ok"] is True
        assert result["runtime_total"] == 0
        assert result["updated_status"] == 0
        assert result["updated_info_hash"] == 0
        assert result["runtime_missing_db"] == 0
        assert result["db_missing_runtime"] == 1
    finally:
        await eng.dispose()
