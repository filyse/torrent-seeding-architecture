"""Слоты переноса: сколько копий сразу уходит с движка и приходит на движок.

Ждущие задачи не занимают слот. Лимит читается в момент, когда слот освобождается,
поэтому смена в настройках действует на очередь без перезапуска API.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager


class EngineSlots:
    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, limit_of: Callable[[], Awaitable[int]]) -> None:
        while True:
            limit = max(1, int(await limit_of()))
            async with self._lock:
                if self._active.get(key, 0) < limit:
                    self._active[key] = self._active.get(key, 0) + 1
                    return
                fut: asyncio.Future = asyncio.get_running_loop().create_future()
                self._waiters.setdefault(key, []).append(fut)
            await fut

    async def release(self, key: str) -> None:
        async with self._lock:
            left = self._active.get(key, 0) - 1
            if left > 0:
                self._active[key] = left
            else:
                self._active.pop(key, None)
            waiters = self._waiters.get(key) or []
            if waiters:
                waiters.pop(0).set_result(None)

    def active(self, key: str) -> int:
        return self._active.get(key, 0)


TRANSFER_SLOTS = EngineSlots()


@asynccontextmanager
async def transfer_slots(source: str, target: str, limit_of: Callable[[], Awaitable[int]]):
    """Занять слот источника и слот приёмника. Одинаковый движок — один слот, без дедлока."""
    keys = [f"src:{source}"]
    if str(target) != str(source):
        keys.append(f"dst:{target}")
    keys.sort()
    acquired: list[str] = []
    try:
        for key in keys:
            await TRANSFER_SLOTS.acquire(key, limit_of)
            acquired.append(key)
        yield
    finally:
        for key in reversed(acquired):
            await TRANSFER_SLOTS.release(key)
