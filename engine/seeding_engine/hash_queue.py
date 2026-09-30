"""Очередь полного хеша на одном движке.

force_recheck в libtorrent возвращается сразу, а диск считает ещё долго.
Слот занят, пока проверка не выйдет из checking. Лишние раздачи ждут.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class HashQueue:
    def __init__(self, limit: int = 2) -> None:
        self._limit = max(1, int(limit))
        self._order: list[int] = []
        self._jobs: dict[int, Callable[[], Awaitable[None]]] = {}
        self._running: set[int] = set()
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def waiting(self) -> list[int]:
        return list(self._order)

    def is_queued(self, db_id: int) -> bool:
        """Ждёт слот хеша: в очереди, но проверка ещё не запущена."""
        return db_id in self._jobs and db_id not in self._running

    def running_count(self) -> int:
        return len(self._running)

    async def set_limit(self, limit: int) -> int:
        async with self._lock:
            self._limit = max(1, int(limit))
            self._pump_locked()
            return self._limit

    async def submit(self, db_id: int, job: Callable[[], Awaitable[None]]) -> str:
        """Поставить хеш. Уже стоящая или идущая раздача второй раз не ставится."""
        async with self._lock:
            if db_id in self._running:
                return "running"
            if db_id in self._jobs:
                return "queued"
            self._jobs[db_id] = job
            self._order.append(db_id)
            started = self._pump_locked()
            return "running" if db_id in started else "queued"

    def _pump_locked(self) -> set[int]:
        started: set[int] = set()
        while self._order and len(self._running) < self._limit:
            db_id = self._order.pop(0)
            job = self._jobs.get(db_id)
            if job is None:
                continue
            self._running.add(db_id)
            started.add(db_id)
            asyncio.create_task(self._wrap(db_id, job))
        return started

    async def _wrap(self, db_id: int, job: Callable[[], Awaitable[None]]) -> None:
        try:
            await job()
        finally:
            async with self._lock:
                self._running.discard(db_id)
                self._jobs.pop(db_id, None)
                self._pump_locked()
