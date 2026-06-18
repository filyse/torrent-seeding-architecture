"""Глобальная политика поиска пиров (DHT/PEX/LSD), общая для всех движков.

Хранится в `app_settings` (ключ `net_policy`, значение — JSON). Применяется к движкам
оркестратором: при изменении — рассылкой на все движки, при саморегистрации движка —
повторным применением (движок мог перезапуститься и сброситься на дефолты из env)."""

from __future__ import annotations

import json

from seeding_db.repository import SettingsRepository

NET_KEY = "net_policy"
DEFAULTS: dict[str, bool] = {"dht": True, "pex": True, "lsd": True}


def _normalize(data: dict) -> dict[str, bool]:
    return {k: bool(data.get(k, DEFAULTS[k])) for k in DEFAULTS}


async def load_net_policy(session) -> dict[str, bool]:
    raw = await SettingsRepository(session).get(NET_KEY)
    if not raw:
        return dict(DEFAULTS)
    try:
        return _normalize(json.loads(raw))
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


async def save_net_policy(session, policy: dict) -> dict[str, bool]:
    merged = _normalize(policy)
    await SettingsRepository(session).set(NET_KEY, json.dumps(merged))
    return merged
