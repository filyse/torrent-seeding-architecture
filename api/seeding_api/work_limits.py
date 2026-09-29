"""Лимиты очередей переноса и хеша. Хранятся в app_settings (ключ work_queues).

Перенос считает оркестратор: не больше N копий сразу с одного движка и на один.
Хеш считает движок: не больше N проверок сразу на этом движке.
Пока в БД пусто — дефолты из env.
"""

from __future__ import annotations

import json
import os

from seeding_db.repository import SettingsRepository

WORK_KEY = "work_queues"
MIGRATE_RANGE = (1, 16)
HASH_RANGE = (1, 8)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_defaults() -> dict[str, int]:
    return {
        "migrate_per_engine": _env_int("SEEDING_MIGRATE_PER_ENGINE", 4),
        "hash_per_engine": _env_int("SEEDING_HASH_PER_ENGINE", 2),
    }


def _clamp(value: object, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def normalize_work_limits(data: dict, *, defaults: dict[str, int] | None = None) -> dict[str, int]:
    base = defaults or env_defaults()
    return {
        "migrate_per_engine": _clamp(
            data.get("migrate_per_engine", base["migrate_per_engine"]),
            MIGRATE_RANGE[0],
            MIGRATE_RANGE[1],
            base["migrate_per_engine"],
        ),
        "hash_per_engine": _clamp(
            data.get("hash_per_engine", base["hash_per_engine"]),
            HASH_RANGE[0],
            HASH_RANGE[1],
            base["hash_per_engine"],
        ),
    }


async def load_work_limits(session) -> dict[str, int]:
    defaults = env_defaults()
    raw = await SettingsRepository(session).get(WORK_KEY)
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return defaults
        return normalize_work_limits(parsed, defaults=defaults)
    except Exception:  # noqa: BLE001
        return defaults


async def save_work_limits(session, data: dict) -> dict[str, int]:
    merged = normalize_work_limits(data)
    await SettingsRepository(session).set(WORK_KEY, json.dumps(merged))
    return merged
