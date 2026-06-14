import pytest
from seeding_db.models import Base, TorrentStatus
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_create_and_list(db_session: AsyncSession):
    repo = TorrentRepository(db_session)
    row = await repo.create(
        display_name="Test",
        save_path="/data/x",
        magnet_uri="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert row.id >= 1
    assert row.status == TorrentStatus.queued.value
    rows = await repo.list_all()
    assert len(rows) == 1
    assert rows[0].display_name == "Test"


@pytest.mark.asyncio
async def test_repository_update_status(db_session: AsyncSession):
    repo = TorrentRepository(db_session)
    row = await repo.create(display_name="a", save_path="/p", magnet_uri="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    updated = await repo.update_status(row.id, TorrentStatus.paused.value)
    assert updated is not None
    assert updated.status == TorrentStatus.paused.value


@pytest.mark.asyncio
async def test_repository_list_for_engine_restore(db_session: AsyncSession):
    repo = TorrentRepository(db_session)
    m = "magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc"
    await repo.create(display_name="q", save_path="/d", magnet_uri=m, status=TorrentStatus.queued.value)
    d = await repo.create(display_name="d", save_path="/d", magnet_uri=m, status=TorrentStatus.downloading.value)
    p = await repo.create(display_name="p", save_path="/d", magnet_uri=m, status=TorrentStatus.paused.value)
    await db_session.commit()
    got = await repo.list_for_engine_restore()
    assert {r.id for r in got} == {d.id, p.id}


@pytest.mark.asyncio
async def test_repository_delete(db_session: AsyncSession):
    repo = TorrentRepository(db_session)
    row = await repo.create(
        display_name="x",
        save_path="/p",
        magnet_uri="magnet:?xt=urn:btih:dddddddddddddddddddddddddddddddddddddddd",
    )
    await db_session.commit()
    assert await repo.delete(row.id) is True
    assert await repo.get_by_id(row.id) is None
    assert await repo.delete(row.id) is False
