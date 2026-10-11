"""Какие движки сейчас недоступны — по фоновому снимку рантайма.

Снимок (`runtime_snapshot.snapshot_once`) раз в ~10 с ходит на каждый движок. Если движок
не ответил ``OFFLINE_AFTER`` раз подряд, его раздачи показываются «Движок недоступен», а не
последним статусом из БД. Статус в БД не трогаем: после возврата движка он снова верный.
"""

from __future__ import annotations

import time

OFFLINE_AFTER = 2  # ~20 с при интервале снимка 10 с: одиночный таймаут не мигает бейджем

_fails: dict[str, int] = {}
_since: dict[str, float] = {}


def mark(engine_id: str, ok: bool) -> None:
    if ok:
        _fails.pop(engine_id, None)
        _since.pop(engine_id, None)
        return
    n = _fails.get(engine_id, 0) + 1
    _fails[engine_id] = n
    if n >= OFFLINE_AFTER:
        _since.setdefault(engine_id, time.time())


def offline_engines() -> set[str]:
    return set(_since)


def is_offline(engine_id: str | None) -> bool:
    return bool(engine_id) and engine_id in _since


def reset() -> None:
    _fails.clear()
    _since.clear()
