"""Очереди переноса и хеша: слоты, лимит из настроек."""

from __future__ import annotations

import asyncio

import pytest
from seeding_api.work_limits import (
    env_defaults,
    load_work_limits,
    normalize_work_limits,
    save_work_limits,
)
from seeding_api.work_queue import EngineSlots, transfer_slots
from seeding_db.models import Base
from seeding_engine.hash_queue import HashQueue
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


def test_normalize_work_limits_clamps():
    out = normalize_work_limits(
        {"migrate_per_engine": 99, "hash_per_engine": 0},
        defaults={"migrate_per_engine": 4, "hash_per_engine": 2},
    )
    assert out == {"migrate_per_engine": 16, "hash_per_engine": 1}


def test_env_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_MIGRATE_PER_ENGINE", "3")
    monkeypatch.setenv("SEEDING_HASH_PER_ENGINE", "1")
    assert env_defaults() == {"migrate_per_engine": 3, "hash_per_engine": 1}


@pytest.mark.asyncio
async def test_save_and_load_work_limits(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEEDING_MIGRATE_PER_ENGINE", raising=False)
    monkeypatch.delenv("SEEDING_HASH_PER_ENGINE", raising=False)
    saved = await save_work_limits(
        db_session, {"migrate_per_engine": 4, "hash_per_engine": 2}
    )
    await db_session.commit()
    assert await load_work_limits(db_session) == saved


@pytest.mark.asyncio
async def test_transfer_slots_limit_source_and_dest():
    slots = EngineSlots()

    async def limit_of() -> int:
        return 1

    started = asyncio.Event()
    release = asyncio.Event()

    first = asyncio.create_task(_hold(slots, "src:a1", limit_of, started, release))
    await started.wait()
    assert slots.active("src:a1") == 1

    second_in = asyncio.Event()

    async def second():
        await slots.acquire("src:a1", limit_of)
        second_in.set()
        await slots.release("src:a1")

    other = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    assert not second_in.is_set()
    release.set()
    await first
    await asyncio.wait_for(other, timeout=1)
    assert second_in.is_set()
    assert slots.active("src:a1") == 0


async def _hold(slots: EngineSlots, key: str, limit_of, started: asyncio.Event, release: asyncio.Event):
    await slots.acquire(key, limit_of)
    started.set()
    await release.wait()
    await slots.release(key)


@pytest.mark.asyncio
async def test_hash_queue_runs_only_limit():
    queue = HashQueue(2)
    running = 0
    peak = 0
    done = asyncio.Event()
    finished = 0

    async def job():
        nonlocal running, peak, finished
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        finished += 1
        if finished == 3:
            done.set()

    await queue.submit(1, job)
    await queue.submit(2, job)
    await queue.submit(3, job)
    assert queue.running_count() == 2
    assert queue.waiting() == [3]
    await asyncio.wait_for(done.wait(), timeout=2)
    assert peak == 2
    assert queue.running_count() == 0
    assert queue.waiting() == []
