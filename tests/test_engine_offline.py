"""Раздачи недоступного движка: флаг engine_online, фильтр и счётчик engine_offline."""

import pytest
from seeding_api import engine_health
from seeding_db.models import Base, TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.session import create_engine, create_session_factory


def test_offline_needs_consecutive_failures():
    engine_health.reset()
    engine_health.mark("x1", False)
    assert not engine_health.is_offline("x1")  # одиночный таймаут не мигает
    engine_health.mark("x1", False)
    assert engine_health.is_offline("x1")
    engine_health.mark("x1", True)
    assert engine_health.offline_engines() == set()


@pytest.mark.asyncio
async def test_filter_and_facets_count_offline_engine(tmp_path):
    eng = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'o.sqlite3'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(eng)
    try:
        async with sf() as s:
            repo = TorrentRepository(s)
            for i, e in enumerate(["a1", "a1", "b1"]):
                row = await repo.create(display_name=f"t{i}", save_path="/d", magnet_uri=None,
                                        status=TorrentStatus.seeding.value)
                row.engine_id = e
            await s.commit()
            rows, total = await repo.list_page(state="engine_offline", offline_engines={"a1"})
            assert total == 2 and {r.engine_id for r in rows} == {"a1"}
            _rows, none = await repo.list_page(state="engine_offline")
            assert none == 0
            f = await repo.facets(offline_engines={"a1"})
            assert f["states"]["engine_offline"] == 2
            # статус в БД не трогаем: после возврата движка он снова верный
            assert f["statuses"]["seeding"] == 3
    finally:
        await eng.dispose()
