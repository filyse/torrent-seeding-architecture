"""Лимиты веб-загрузки файлов. Хранятся в app_settings (ключ upload_limits).

Правит только admin. Чтение — любой авторизованный (очередь в UI).
Пока в БД пусто — дефолты из env."""

from __future__ import annotations

import json
import os

from seeding_db.repository import SettingsRepository

UPLOAD_KEY = "upload_limits"
MAX_PARALLEL_RANGE = (1, 8)
CHUNK_CONC_RANGE = (1, 8)


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
        "max_parallel_uploads": _env_int("SEEDING_UPLOAD_MAX_PARALLEL", 4),
        "chunk_concurrency": _env_int("SEEDING_UPLOAD_CHUNK_CONCURRENCY", 4),
    }


def _clamp(value: object, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def normalize_upload_limits(data: dict, *, defaults: dict[str, int] | None = None) -> dict[str, int]:
    base = defaults or env_defaults()
    return {
        "max_parallel_uploads": _clamp(
            data.get("max_parallel_uploads", base["max_parallel_uploads"]),
            MAX_PARALLEL_RANGE[0],
            MAX_PARALLEL_RANGE[1],
            base["max_parallel_uploads"],
        ),
        "chunk_concurrency": _clamp(
            data.get("chunk_concurrency", base["chunk_concurrency"]),
            CHUNK_CONC_RANGE[0],
            CHUNK_CONC_RANGE[1],
            base["chunk_concurrency"],
        ),
    }


async def load_upload_limits(session) -> dict[str, int]:
    defaults = env_defaults()
    raw = await SettingsRepository(session).get(UPLOAD_KEY)
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return defaults
        return normalize_upload_limits(parsed, defaults=defaults)
    except Exception:  # noqa: BLE001
        return defaults


async def save_upload_limits(session, data: dict) -> dict[str, int]:
    merged = normalize_upload_limits(data)
    await SettingsRepository(session).set(UPLOAD_KEY, json.dumps(merged))
    return merged
