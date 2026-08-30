"""Слоты unchoke и алгоритм сидирования. Без libtorrent — только нормализация."""

from __future__ import annotations

import os

SEED_CHOKING_NAMES = ("round_robin", "fastest_upload", "anti_leech")
SEED_CHOKING_ALIASES = {
    "0": "round_robin",
    "1": "fastest_upload",
    "2": "anti_leech",
    "round-robin": "round_robin",
    "fastest": "fastest_upload",
    "anti-leech": "anti_leech",
}
# Дефолт libtorrent 2.x: 8 слотов, держать самого быстрого.
DEFAULT_UNCHOKE: dict = {
    "unchoke_slots_limit": 8,
    "seed_choking_algorithm": "fastest_upload",
}


def normalize_unchoke_settings(data: dict | None) -> dict:
    slots = DEFAULT_UNCHOKE["unchoke_slots_limit"]
    algo = DEFAULT_UNCHOKE["seed_choking_algorithm"]
    raw = data or {}
    if raw.get("unchoke_slots_limit") not in (None, ""):
        try:
            slots = int(raw["unchoke_slots_limit"])
        except (TypeError, ValueError):
            slots = DEFAULT_UNCHOKE["unchoke_slots_limit"]
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


def unchoke_settings_for_lt(settings: dict | None) -> dict[str, int]:
    norm = normalize_unchoke_settings(settings)
    return {
        "unchoke_slots_limit": int(norm["unchoke_slots_limit"]),
        "seed_choking_algorithm": SEED_CHOKING_NAMES.index(norm["seed_choking_algorithm"]),
    }


def env_unchoke_settings() -> dict:
    raw: dict = {}
    slots = os.getenv("LT_UNCHOKE_SLOTS_LIMIT", "").strip()
    if slots:
        raw["unchoke_slots_limit"] = slots
    algo = os.getenv("LT_SEED_CHOKING_ALGORITHM", "").strip()
    if algo:
        raw["seed_choking_algorithm"] = algo
    return normalize_unchoke_settings(raw or dict(DEFAULT_UNCHOKE))
