import time

import pytest
from seeding_api.runtime_snapshot import adopt_candidates, snapshot_once
from seeding_db.models import Base, TorrentStatus
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

OLD = int(time.time()) - 3600


def handle(db_id: int, **kw: object) -> dict:
    base: dict = {
        "db_id": db_id,
        "name": f"t{db_id}",
        "save_path": "/data/a2",
        "info_hash": f"{db_id:040x}",
        "runtime_status": "active",
        "lt_state": "seeding",
        "progress": 1.0,
        "size": 1000,
        "added_time": OLD,
        "upload_rate": 0,
        "download_rate": 0,
        "peers": 0,
        "num_seeds": 1,
        "num_leechers": 0,
        "check_rate": 0,
        "total_uploaded": 0,
    }
    base.update(kw)
    return base


class _Row:
    def __init__(self, torrent_id: int, info_hash: str) -> None:
        self.id = torrent_id
        self.info_hash = info_hash


def test_adopt_skips_known_hash_fresh_handle_and_duplicate_copy():
    old = handle(5, info_hash="bb", name="kept")
    found = adopt_candidates(
        [_Row(1, "aa")],
        {
            "a2": {
                1: handle(1, info_hash="aa"),
                5: old,
                6: handle(6, info_hash="AA"),
                7: handle(7, info_hash="cc", added_time=int(time.time())),
                8: handle(8, info_hash="bb"),
            }
        },
    )
    assert [item[1] for item in found] == [5]


class _Pool:
    def __init__(self, handles: dict) -> None:
        self.handles = handles

    def client_for(self, _eid: str) -> "_Pool":
        return self

    async def list_runtime(self) -> dict:
        return self.handles


@pytest.mark.asyncio
async def test_snapshot_adopts_missing_torrent_and_not_its_copy():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        row = await TorrentRepository(session).create(
            display_name="known",
            save_path="/data/a2",
            info_hash="aa" * 20,
            engine_id="a2",
        )
        await session.commit()
        known_id = row.id

    handles = {
        known_id: handle(known_id, info_hash="aa" * 20, size=50),
        50: handle(50, info_hash="bb" * 20, name="orphan", size=777),
        51: handle(51, info_hash="bb" * 20, name="copy", size=1),
    }
    await snapshot_once(_Pool(handles), factory)
    await snapshot_once(_Pool(handles), factory)

    async with factory() as session:
        repo = TorrentRepository(session)
        rows = await repo.list_all()
        assert {r.id for r in rows} == {known_id, 50}
        adopted = next(r for r in rows if r.id == 50)
        assert adopted.display_name == "orphan"
        assert adopted.engine_id == "a2"
        assert adopted.size == 777
        assert adopted.status == TorrentStatus.seeding.value
        facets = await repo.facets()
        assert facets["total"] == 2
        assert facets["engines"]["a2"] == 2
        assert facets["engine_sizes"]["a2"] == 50 + 777
    await engine.dispose()
