"""Сиды роя, уже снятые скрейпом. None — цифры трекера ещё нет."""

from __future__ import annotations


def display_seed_count(num_complete: int | None) -> int | None:
    if num_complete is None:
        return None
    try:
        n = int(num_complete)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n
