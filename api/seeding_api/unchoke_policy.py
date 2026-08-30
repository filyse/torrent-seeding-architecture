"""Глобальная политика раздачи: сколько пиров кормить сразу и каким алгоритмом.

Хранится в `app_settings` (ключ `unchoke_policy`). Оркестратор шлёт на движки
при POST и при саморегистрации. Откат: вернуть 8 + fastest_upload (дефолт lt)."""

from __future__ import annotations

import json

from seeding_db.repository import SettingsRepository

UNCHOKE_KEY = "unchoke_policy"
SEED_CHOKING_NAMES = ("round_robin", "fastest_upload", "anti_leech")
SEED_CHOKING_ALIASES = {
    "0": "round_robin",
    "1": "fastest_upload",
    "2": "anti_leech",
    "round-robin": "round_robin",
    "fastest": "fastest_upload",
    "anti-leech": "anti_leech",
}
DEFAULTS: dict = {
    "unchoke_slots_limit": 8,
    "seed_choking_algorithm": "fastest_upload",
}


def normalize_unchoke_policy(data: dict | None) -> dict:
    slots = DEFAULTS["unchoke_slots_limit"]
    algo = DEFAULTS["seed_choking_algorithm"]
    raw = data or {}
    if raw.get("unchoke_slots_limit") not in (None, ""):
        try:
            slots = int(raw["unchoke_slots_limit"])
        except (TypeError, ValueError):
            slots = DEFAULTS["unchoke_slots_limit"]
        if slots < -1:
            slots = -1
        if slots > 256:
            slots = 256
    if raw.get("seed_choking_algorithm") not in (None, ""):
        key = str(raw["seed_choking_algorithm"]).strip().lower()
        key = SEED_CHOKING_ALIASES.get(key, key)
        if key in SEED_CHOKING_NAMES:
            algo = key
    return {"unchoke_slots_limit": slots, "seed_choking_algorithm": algo}


async def load_unchoke_policy(session) -> dict:
    raw = await SettingsRepository(session).get(UNCHOKE_KEY)
    if not raw:
        return dict(DEFAULTS)
    try:
        return normalize_unchoke_policy(json.loads(raw))
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


async def save_unchoke_policy(session, policy: dict) -> dict:
    merged = normalize_unchoke_policy(policy)
    await SettingsRepository(session).set(UNCHOKE_KEY, json.dumps(merged))
    return merged
