"""Одна дисковая очередь HDD-движка.

Слоты общие для копирования, сборки загрузки и проверки. Перенос и сборка
(band=user) стартуют раньше ожидающих проверок. Уже идущая проверка не
прерывается. Снижение лимита не останавливает то, что уже началось.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

OnStart = Callable[[], Awaitable[None]]


def _clamp(limit: int) -> int:
    return max(1, min(8, int(limit)))


@dataclass
class _Job:
    id: str
    band: str
    kind: str
    torrent_id: int | None
    leased: bool
    on_start: OnStart | None
    state: str = "queued"
    touched: float = field(default_factory=time.monotonic)


class DiskQueue:
    def __init__(
        self,
        limit: int = 4,
        *,
        ttl: float = 90.0,
        admit_running: Callable[[], int] | None = None,
    ) -> None:
        self.limit = _clamp(limit)
        self.ttl = ttl
        self._admit_running = admit_running or (lambda: 0)
        self._user: list[_Job] = []
        self._check: list[_Job] = []
        self._running: dict[str, _Job] = {}
        self._jobs: dict[str, _Job] = {}
        self._by_torrent: dict[int, str] = {}
        self._waiters: dict[str, asyncio.Future[None]] = {}
        self._lock = asyncio.Lock()

    async def set_limit(self, limit: int) -> int:
        async with self._lock:
            self.limit = _clamp(limit)
            applied = self.limit
        await self.pump()
        return applied

    async def acquire(
        self,
        job_id: str,
        *,
        band: str,
        kind: str,
        torrent_id: int | None = None,
        on_start: OnStart | None = None,
        leased: bool = False,
    ) -> str:
        async with self._lock:
            existing = self._jobs.get(job_id)
            if existing is not None:
                existing.touched = time.monotonic()
            elif torrent_id is None or torrent_id not in self._by_torrent:
                job = _Job(
                    id=job_id,
                    band="user" if band == "user" else "check",
                    kind=kind,
                    torrent_id=torrent_id,
                    leased=leased,
                    on_start=on_start,
                )
                self._jobs[job_id] = job
                if torrent_id is not None:
                    self._by_torrent[torrent_id] = job_id
                (self._user if job.band == "user" else self._check).append(job)
        await self.pump()
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return job.state
            if torrent_id is not None:
                other = self._jobs.get(self._by_torrent.get(torrent_id, ""))
                if other is not None:
                    return other.state
            return "missing"

    async def renew(self, job_id: str) -> str:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "missing"
            job.touched = time.monotonic()
            return job.state

    async def release(self, job_id: str) -> None:
        async with self._lock:
            self._drop(job_id)
        await self.pump()

    async def poll(self, job_id: str) -> str:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.state if job is not None else "missing"

    async def holds_torrent(self, torrent_id: int) -> bool:
        async with self._lock:
            job_id = self._by_torrent.get(torrent_id)
            if job_id is None:
                return False
            job = self._running.get(job_id)
            return job is not None and job.band == "user"

    async def is_check_queued(self, torrent_id: int) -> bool:
        async with self._lock:
            job_id = self._by_torrent.get(torrent_id)
            if job_id is None:
                return False
            job = self._jobs.get(job_id)
            return job is not None and job.band == "check" and job.state == "queued"

    async def expire(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        async with self._lock:
            dead = [
                job.id
                for job in (*self._running.values(), *self._user, *self._check)
                if job.leased and now - job.touched > self.ttl
            ]
            for job_id in dead:
                self._drop(job_id)
        if dead:
            await self.pump()
        return len(dead)

    async def pump(self) -> None:
        started: list[_Job] = []
        admit_running = self._admit_running()
        async with self._lock:
            while self._user and admit_running + len(self._running) < self.limit:
                started.append(self._take(self._user))
            while (
                self._check
                and not self._user
                and admit_running + len(self._running) < self.limit
            ):
                started.append(self._take(self._check))
        for job in started:
            self._wake(job.id)
            if job.on_start is not None:
                asyncio.create_task(self._run(job))

    def blocks_checks(self) -> int:
        """Уже занятые слоты и переносы, которые ещё ждут. CheckAdmit вычитает их из лимита."""
        return len(self._running) + len(self._user)

    async def wait_change(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state == "running":
                return
            fut = self._waiters.get(job_id)
            if fut is None or fut.done():
                fut = asyncio.get_running_loop().create_future()
                self._waiters[job_id] = fut
        await fut

    def _take(self, queue: list[_Job]) -> _Job:
        job = queue.pop(0)
        job.state = "running"
        job.touched = time.monotonic()
        self._running[job.id] = job
        return job

    def _drop(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        self._running.pop(job_id, None)
        if job is None:
            return
        self._user = [item for item in self._user if item.id != job_id]
        self._check = [item for item in self._check if item.id != job_id]
        if job.torrent_id is not None and self._by_torrent.get(job.torrent_id) == job_id:
            self._by_torrent.pop(job.torrent_id, None)
        self._wake(job_id)

    def _wake(self, job_id: str) -> None:
        fut = self._waiters.pop(job_id, None)
        if fut is not None and not fut.done():
            fut.set_result(None)

    async def _run(self, job: _Job) -> None:
        assert job.on_start is not None
        try:
            await job.on_start()
        except Exception:
            log.exception("disk job %s failed", job.id)
        finally:
            await self.release(job.id)
