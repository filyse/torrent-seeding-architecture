"""Мягкий режим обслуживания (для restore БД).

Пока режим включён, API отвечает 503 на «рабочие» маршруты (раздачи, движки, очередь),
чтобы клиенты не дёргали БД во время восстановления. Здоровье, авторизация и сами бэкапы
остаются доступными. Флаг — в памяти процесса (uvicorn запущен одним воркером) и с TTL,
чтобы автоматически сняться, если что-то пошло не так.
"""

from __future__ import annotations

import time

_until: float = 0.0
_reason: str = ""


def begin(reason: str, ttl_seconds: float = 600.0) -> None:
    global _until, _reason
    _until = time.monotonic() + max(1.0, ttl_seconds)
    _reason = reason


def end() -> None:
    global _until, _reason
    _until = 0.0
    _reason = ""


def status() -> tuple[bool, str]:
    if time.monotonic() < _until:
        return True, _reason
    return False, ""
