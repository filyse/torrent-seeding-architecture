"""Дельты upload_samples и rollup день/неделя для графиков /network/uploaded."""

from datetime import datetime, timedelta, timezone

import pytest
from seeding_db.models import Base
from seeding_db.repository import UploadSampleRepository
from seeding_db.upload_history import (
    bucket_is_sampled,
    build_sample_rows,
    counter_delta,
    history_from_samples,
    merge_engine_totals,
    period_windows,
    rollup_series,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

GB = 1024**3
UTC = timezone.utc


def ts(y, m, d, h=0, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_delta_first_sample_is_zero():
    assert counter_delta(None, 100) == 0


def test_delta_growing_counter():
    assert counter_delta(8 * GB, 9 * GB) == 1 * GB


def test_delta_reset_uses_new_value():
    """Счётчик уехал назад (рестарт/перенос) — берём новое значение как дельту."""
    assert counter_delta(100 * GB, 3 * GB) == 3 * GB


def test_offline_engine_reuses_last_sample():
    merged = merge_engine_totals({"b1": 10}, {"b1": 8, "b2": 5}, {"b1", "b2"})
    assert merged == {"b1": 10, "b2": 5}


def test_removed_engine_is_dropped():
    merged = merge_engine_totals({"b1": 10}, {"b1": 8, "gone": 99}, {"b1"})
    assert merged == {"b1": 10}


def test_build_rows_farm_and_wan():
    t = ts(2026, 8, 24, 12)
    rows = build_sample_rows(t, {"b1": 100, "a1": 40}, {"b1": "wan1", "a1": "wan2"})
    by = {(scope, sid): val for _, scope, sid, val in rows}
    assert by[("engine", "b1")] == 100
    assert by[("engine", "a1")] == 40
    assert by[("wan", "wan1")] == 100
    assert by[("wan", "wan2")] == 40
    assert by[("farm", "")] == 140


def test_day_windows_are_24_hourly_buckets():
    now = ts(2026, 8, 24, 15, 30)
    step, buckets, prev = period_windows("day", now)
    assert step == timedelta(hours=1)
    assert len(buckets) == 24
    assert buckets[-1] == ts(2026, 8, 24, 15)
    assert buckets[0] == ts(2026, 8, 23, 16)
    assert prev[-1] == ts(2026, 8, 23, 15)
    assert len(prev) == 24


def test_week_windows_are_7_daily_buckets():
    now = ts(2026, 8, 24, 15, 30)
    step, buckets, prev = period_windows("week", now)
    assert step == timedelta(days=1)
    assert len(buckets) == 7
    assert buckets[-1] == ts(2026, 8, 24)
    assert buckets[0] == ts(2026, 8, 18)
    assert prev[0] == ts(2026, 8, 11)
    assert prev[-1] == ts(2026, 8, 17)


def test_month_windows_are_30_daily_buckets():
    now = ts(2026, 8, 24, 10)
    step, buckets, _prev = period_windows("month", now)
    assert step == timedelta(days=1)
    assert len(buckets) == 30
    assert buckets[-1] == ts(2026, 8, 24)
    assert buckets[0] == ts(2026, 7, 26)


def test_hourly_rollup_sums_deltas_in_bucket():
    origin = ts(2026, 8, 24, 14)
    buckets = [origin, origin + timedelta(hours=1)]
    points = [
        (origin - timedelta(minutes=15), 1000),
        (origin + timedelta(minutes=10), 1000 + 2 * GB),
        (origin + timedelta(minutes=50), 1000 + 3 * GB),
        (origin + timedelta(hours=1, minutes=5), 1000 + 5 * GB),
    ]
    out = rollup_series(points, buckets, timedelta(hours=1))
    assert out[0] == 3 * GB
    assert out[1] == 2 * GB


def test_rollup_reset_in_bucket():
    origin = ts(2026, 8, 24, 14)
    buckets = [origin]
    points = [
        (origin - timedelta(minutes=5), 50 * GB),
        (origin + timedelta(minutes=10), 2 * GB),
    ]
    assert rollup_series(points, buckets, timedelta(hours=1)) == [2 * GB]


def test_history_from_samples_day_and_previous_window():
    """Два движка на разных WAN: ферма = сумма, сравнение с прошлыми сутками."""
    now = ts(2026, 8, 24, 15, 30)
    # База до предыдущего окна и рост в прошлом окне + в текущем.
    samples = [
        (ts(2026, 8, 22, 15), "engine", "b1", 10 * GB),
        (ts(2026, 8, 22, 15), "engine", "a1", 4 * GB),
        (ts(2026, 8, 23, 10), "engine", "b1", 14 * GB),  # +4 GB вчера
        (ts(2026, 8, 23, 10), "engine", "a1", 5 * GB),  # +1 GB вчера
        (ts(2026, 8, 24, 10), "engine", "b1", 20 * GB),  # +6 GB сегодня
        (ts(2026, 8, 24, 10), "engine", "a1", 7 * GB),  # +2 GB сегодня
    ]
    hist = history_from_samples(
        samples,
        "day",
        now,
        wan_ids=["wan1", "wan2"],
        engine_wan={"b1": "wan1", "a1": "wan2"},
    )
    assert hist.total.farm == 8 * GB
    assert hist.total.wan["wan1"] == 6 * GB
    assert hist.total.wan["wan2"] == 2 * GB
    assert hist.previous_total.farm == 5 * GB
    assert hist.previous_total.wan["wan1"] == 4 * GB
    assert sum(b.farm for b in hist.buckets) == hist.total.farm
    assert all("b1" in b.engines for b in hist.buckets)


def test_history_single_engine_reset_does_not_inflate_other_wan():
    now = ts(2026, 8, 24, 12, 0)
    samples = [
        (ts(2026, 8, 24, 10, 0), "engine", "b1", 100 * GB),
        (ts(2026, 8, 24, 10, 0), "engine", "a1", 50 * GB),
        (ts(2026, 8, 24, 11, 0), "engine", "b1", 2 * GB),  # сброс b1
        (ts(2026, 8, 24, 11, 0), "engine", "a1", 51 * GB),  # +1 GB
    ]
    hist = history_from_samples(
        samples,
        "day",
        now,
        wan_ids=["wan1", "wan2"],
        engine_wan={"b1": "wan1", "a1": "wan2"},
    )
    hour_11 = next(b for b in hist.buckets if b.t == ts(2026, 8, 24, 11))
    assert hour_11.wan["wan1"] == 2 * GB
    assert hour_11.wan["wan2"] == 1 * GB
    assert hour_11.farm == 3 * GB


def test_single_sample_marks_only_that_bucket_sampled():
    now = ts(2026, 8, 24, 15, 30)
    hist = history_from_samples(
        [(ts(2026, 8, 24, 9, 54), "engine", "b1", 10 * GB)],
        "day",
        now,
        wan_ids=["wan1"],
        engine_wan={"b1": "wan1"},
    )
    marked = [b for b in hist.buckets if b.sampled]
    assert len(marked) == 1
    assert marked[0].t == ts(2026, 8, 24, 9)
    assert hist.first_sampled_at == ts(2026, 8, 24, 9, 54)
    assert all(not b.sampled for b in hist.buckets if b.t != ts(2026, 8, 24, 9))


def test_bucket_coverage_between_first_and_last():
    step = timedelta(hours=1)
    first = ts(2026, 8, 24, 10, 5)
    last = ts(2026, 8, 24, 12, 20)
    assert bucket_is_sampled(ts(2026, 8, 24, 10), step, first, last)
    assert bucket_is_sampled(ts(2026, 8, 24, 12), step, first, last)
    assert not bucket_is_sampled(ts(2026, 8, 24, 9), step, first, last)
    assert not bucket_is_sampled(ts(2026, 8, 24, 13), step, first, last)


def test_week_rollup_from_daily_points():
    now = ts(2026, 8, 24, 18)
    samples = [
        (ts(2026, 8, 17, 12), "engine", "b1", 0),
        (ts(2026, 8, 18, 12), "engine", "b1", 1 * GB),
        (ts(2026, 8, 19, 12), "engine", "b1", 3 * GB),
        (ts(2026, 8, 24, 12), "engine", "b1", 10 * GB),
    ]
    hist = history_from_samples(samples, "week", now, wan_ids=["wan1"], engine_wan={"b1": "wan1"})
    assert hist.total.wan["wan1"] == 10 * GB
    assert hist.buckets[0].t == ts(2026, 8, 18)
    assert hist.buckets[0].farm == 1 * GB
    assert hist.buckets[1].farm == 2 * GB
    assert hist.buckets[-1].farm == 7 * GB


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
async def test_repository_history_reads_engine_rows(db_session: AsyncSession):
    repo = UploadSampleRepository(db_session)
    now = ts(2026, 8, 24, 15, 30)
    rows = build_sample_rows(ts(2026, 8, 24, 10), {"b1": 4 * GB}, {"b1": "wan1"})
    rows += build_sample_rows(ts(2026, 8, 24, 14), {"b1": 7 * GB}, {"b1": "wan1"})
    await repo.insert_many(rows)
    hist = await repo.history(
        period="day",
        now=now,
        wan_ids=["wan1", "wan2"],
        engine_wan={"b1": "wan1"},
    )
    assert hist.total.farm == 3 * GB
    assert hist.total.wan["wan1"] == 3 * GB
    assert hist.total.wan["wan2"] == 0


@pytest.mark.asyncio
async def test_repository_download_metric_skips_null(db_session: AsyncSession):
    repo = UploadSampleRepository(db_session)
    now = ts(2026, 8, 24, 15, 30)
    await repo.insert_many(build_sample_rows(ts(2026, 8, 24, 10), {"b1": 4 * GB}, {"b1": "wan1"}))
    await repo.insert_many(
        [(ts(2026, 8, 24, 14), "engine", "b1", 7 * GB, 2 * GB)],
    )
    up = await repo.history(
        period="day", now=now, wan_ids=["wan1"], engine_wan={"b1": "wan1"}, metric="uploaded"
    )
    down = await repo.history(
        period="day", now=now, wan_ids=["wan1"], engine_wan={"b1": "wan1"}, metric="downloaded"
    )
    assert up.total.farm == 3 * GB
    assert down.total.farm == 0
    assert down.first_sampled_at == ts(2026, 8, 24, 14)
