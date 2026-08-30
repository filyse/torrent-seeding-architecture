"""Политика unchoke: нормализация и persist. Без libtorrent."""

from __future__ import annotations

import pytest
from seeding_api.unchoke_policy import (
    DEFAULTS,
    load_unchoke_policy,
    normalize_unchoke_policy,
    save_unchoke_policy,
)
from seeding_db.models import Base
from seeding_engine.unchoke import (
    DEFAULT_UNCHOKE,
    env_unchoke_settings,
    normalize_unchoke_settings,
    unchoke_settings_for_lt,
)
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


def test_normalize_defaults():
    assert normalize_unchoke_settings(None) == DEFAULT_UNCHOKE
    assert normalize_unchoke_policy({}) == DEFAULTS


def test_normalize_round_robin_and_clamp():
    out = normalize_unchoke_settings(
        {"unchoke_slots_limit": 32, "seed_choking_algorithm": "round_robin"}
    )
    assert out == {"unchoke_slots_limit": 32, "seed_choking_algorithm": "round_robin"}
    packed = unchoke_settings_for_lt(out)
    assert packed["unchoke_slots_limit"] == 32
    assert packed["seed_choking_algorithm"] == 0


def test_normalize_aliases_and_junk():
    assert normalize_unchoke_settings({"seed_choking_algorithm": "0"})[
        "seed_choking_algorithm"
    ] == "round_robin"
    assert normalize_unchoke_settings({"unchoke_slots_limit": 999})["unchoke_slots_limit"] == 256
    assert normalize_unchoke_settings({"unchoke_slots_limit": -8})["unchoke_slots_limit"] == -1
    assert normalize_unchoke_settings({"seed_choking_algorithm": "nope"})[
        "seed_choking_algorithm"
    ] == "fastest_upload"


def test_env_unchoke(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LT_UNCHOKE_SLOTS_LIMIT", "32")
    monkeypatch.setenv("LT_SEED_CHOKING_ALGORITHM", "round_robin")
    assert env_unchoke_settings() == {
        "unchoke_slots_limit": 32,
        "seed_choking_algorithm": "round_robin",
    }


@pytest.mark.asyncio
async def test_policy_roundtrip(db_session: AsyncSession):
    assert await load_unchoke_policy(db_session) == DEFAULTS
    saved = await save_unchoke_policy(
        db_session,
        {"unchoke_slots_limit": 32, "seed_choking_algorithm": "round_robin"},
    )
    await db_session.commit()
    assert saved["seed_choking_algorithm"] == "round_robin"
    assert await load_unchoke_policy(db_session) == saved
    await save_unchoke_policy(
        db_session,
        {"unchoke_slots_limit": 8, "seed_choking_algorithm": "fastest_upload"},
    )
    await db_session.commit()
    assert await load_unchoke_policy(db_session) == DEFAULTS
