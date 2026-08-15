"""Лимиты веб-загрузки: нормализация, persist в app_settings, env-fallback."""

from __future__ import annotations

import pytest
from seeding_api.upload_limits import (
    env_defaults,
    load_upload_limits,
    normalize_upload_limits,
    save_upload_limits,
)
from seeding_db.models import Base
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


def test_normalize_clamps_and_defaults():
    out = normalize_upload_limits(
        {"max_parallel_uploads": 99, "chunk_concurrency": 0},
        defaults={"max_parallel_uploads": 4, "chunk_concurrency": 4},
    )
    assert out == {"max_parallel_uploads": 8, "chunk_concurrency": 1}


def test_normalize_junk_falls_back():
    out = normalize_upload_limits(
        {"max_parallel_uploads": "nope", "chunk_concurrency": None},
        defaults={"max_parallel_uploads": 3, "chunk_concurrency": 2},
    )
    assert out == {"max_parallel_uploads": 3, "chunk_concurrency": 2}


def test_env_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_UPLOAD_MAX_PARALLEL", "1")
    monkeypatch.setenv("SEEDING_UPLOAD_CHUNK_CONCURRENCY", "2")
    assert env_defaults() == {"max_parallel_uploads": 1, "chunk_concurrency": 2}


@pytest.mark.asyncio
async def test_load_empty_uses_env(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_UPLOAD_MAX_PARALLEL", "1")
    monkeypatch.setenv("SEEDING_UPLOAD_CHUNK_CONCURRENCY", "3")
    assert await load_upload_limits(db_session) == {
        "max_parallel_uploads": 1,
        "chunk_concurrency": 3,
    }


@pytest.mark.asyncio
async def test_save_and_load_roundtrip(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEEDING_UPLOAD_MAX_PARALLEL", raising=False)
    monkeypatch.delenv("SEEDING_UPLOAD_CHUNK_CONCURRENCY", raising=False)
    saved = await save_upload_limits(
        db_session,
        {"max_parallel_uploads": 1, "chunk_concurrency": 2},
    )
    await db_session.commit()
    assert saved == {"max_parallel_uploads": 1, "chunk_concurrency": 2}
    assert await load_upload_limits(db_session) == saved


@pytest.mark.asyncio
async def test_load_ignores_corrupt_json(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    from seeding_db.repository import SettingsRepository

    monkeypatch.delenv("SEEDING_UPLOAD_MAX_PARALLEL", raising=False)
    monkeypatch.delenv("SEEDING_UPLOAD_CHUNK_CONCURRENCY", raising=False)
    await SettingsRepository(db_session).set("upload_limits", "not-json")
    await db_session.commit()
    assert await load_upload_limits(db_session) == {
        "max_parallel_uploads": 4,
        "chunk_concurrency": 4,
    }
