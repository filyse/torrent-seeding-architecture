"""«Всего отдано» принадлежит раздаче, а не движку.

Счётчик libtorrent живёт в рамках одной инкарнации торрента: перенос на другой движок
добавляет раздачу заново, и счётчик стартует с нуля. Раньше снимок рантайма ЗЕРКАЛИЛ его
в БД, поэтому после переноса объём отданного обнулялся. Здесь проверяем накопитель.
"""

import pytest
from seeding_api.runtime_sync import (
    accumulate_uploaded,
    apply_uploaded_carry,
    uploaded_with_carry,
)
from seeding_db.models import Base
from seeding_db.repository import TorrentRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

GB = 1024**3


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


class _Row:
    """Минимальная замена TorrentRecord для проверки чтения."""

    def __init__(self, uploaded_total: int, uploaded_seen: int):
        self.uploaded_total = uploaded_total
        self.uploaded_seen = uploaded_seen


def test_growing_counter_accumulates_delta_not_absolute():
    total, seen = accumulate_uploaded(total=10 * GB, seen=8 * GB, current=9 * GB)
    assert total == 11 * GB  # прибавилась дельта 1 ГБ, а не абсолютные 9
    assert seen == 9 * GB


def test_counter_reset_keeps_history():
    """Перенос/потеря fastresume: счётчик уехал назад — накопленное не теряем."""
    total, seen = accumulate_uploaded(total=100 * GB, seen=100 * GB, current=0)
    assert total == 100 * GB
    assert seen == 0

    # Дальше движок отдаёт уже на новом месте — прибавляется поверх истории.
    total, seen = accumulate_uploaded(total, seen, current=3 * GB)
    assert total == 103 * GB
    assert seen == 3 * GB


def test_invariant_total_minus_seen_is_previous_engines():
    """total - seen = отдано на прошлых движках. На этом держится чтение между снимками."""
    total, seen = 0, 0
    for current in (1 * GB, 5 * GB, 12 * GB):
        total, seen = accumulate_uploaded(total, seen, current)
    assert total - seen == 0  # одна инкарнация, истории нет

    total, seen = accumulate_uploaded(total, seen, current=0)  # перенос
    assert total - seen == 12 * GB
    total, seen = accumulate_uploaded(total, seen, current=2 * GB)
    assert total - seen == 12 * GB
    assert total == 14 * GB


def test_read_time_matches_snapshot_result():
    """Чтение и снимок обязаны сходиться, иначе цифра «прыгает» на каждом проходе."""
    cases = [
        (10 * GB, 8 * GB, 9 * GB),
        (100 * GB, 100 * GB, 0),
        (100 * GB, 100 * GB, 3 * GB),
        (0, 0, 0),
    ]
    for total, seen, current in cases:
        assert uploaded_with_carry(total, seen, current) == accumulate_uploaded(
            total, seen, current
        )[0]


def test_read_time_survives_migration_before_next_snapshot():
    """Сразу после переноса, до фонового прохода: показываем историю + живое значение."""
    row = _Row(uploaded_total=50 * GB, uploaded_seen=50 * GB)
    runtime = apply_uploaded_carry(row, {"total_uploaded": 0, "upload_rate": 0})
    assert runtime["total_uploaded"] == 50 * GB  # не ноль

    runtime = apply_uploaded_carry(row, {"total_uploaded": 512 * 1024**2})
    assert runtime["total_uploaded"] == 50 * GB + 512 * 1024**2


def test_carry_is_noop_without_runtime():
    row = _Row(uploaded_total=7 * GB, uploaded_seen=7 * GB)
    assert apply_uploaded_carry(row, None) is None
    assert apply_uploaded_carry(row, {}) == {}


def test_negative_and_missing_values_do_not_corrupt_total():
    assert accumulate_uploaded(total=None, seen=None, current=None) == (0, 0)
    assert accumulate_uploaded(total=5 * GB, seen=-1, current=-1) == (5 * GB, 0)


@pytest.mark.asyncio
async def test_engine_switch_resets_baseline_but_keeps_total(db_session: AsyncSession):
    """update_engine — известная граница инкарнации: обнуляем базу дельт, не историю."""
    repo = TorrentRepository(db_session)
    row = await repo.create(
        display_name="show",
        save_path="/data/a1/show",
        magnet_uri="magnet:?xt=urn:btih:dddddddddddddddddddddddddddddddddddddddd",
        engine_id="a1",
    )
    row.uploaded_total = 80 * GB
    row.uploaded_seen = 80 * GB
    await db_session.flush()

    moved = await repo.update_engine(row.id, "b3", "/data/b3/show")
    assert moved is not None
    assert moved.engine_id == "b3"
    assert moved.uploaded_total == 80 * GB  # объём привязан к раздаче
    assert moved.uploaded_seen == 0  # счётчик нового движка стартует с нуля

    # Первый тик на новом движке прибавляется к истории, а не заменяет её.
    total, seen = accumulate_uploaded(
        moved.uploaded_total, moved.uploaded_seen, current=1 * GB
    )
    assert total == 81 * GB
    assert seen == 1 * GB
