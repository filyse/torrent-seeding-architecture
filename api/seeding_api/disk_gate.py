"""Слоты HDD на время переноса.

SSD остаётся на очереди переноса. HDD и unknown занимают слот диска движка.
Слоты берутся в порядке id движка: встречные копии не держат друг друга.
Если второй слот не дали, первый отпускается. 404 у движка — очереди нет,
эта сторона идёт по старой очереди переноса.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

StillQueued = Callable[[], Awaitable[bool]]


def uses_disk_queue(kind: str | None) -> bool:
    return str(kind or "unknown").strip().lower() != "ssd"


async def disk_kind_of(client) -> str:
    try:
        stats = await client.session_stats()
    except Exception:
        log.warning("disk kind unavailable, treating engine as hdd")
        return "unknown"
    if not isinstance(stats, dict):
        return "unknown"
    kind = str(stats.get("disk_kind") or "unknown").strip().lower()
    return kind or "unknown"


class DiskHold:
    """Уже выданные слоты. Источник отпускается сразу после чтения, приёмник — после хеша."""

    def __init__(self) -> None:
        self.source_job: str | None = None
        self.dest_job: str | None = None
        self._items: list[dict] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def add(self, *, role: str, client, job: str) -> None:
        self._items.append({"role": role, "client": client, "job": job})
        if role == "src":
            self.source_job = job
        else:
            self.dest_job = job

    def start_renew(self) -> None:
        if self._items and self._task is None:
            self._task = asyncio.create_task(self._renew())

    async def release_source(self) -> None:
        if self.source_job is None or self.source_job == self.dest_job:
            return
        await self._release_role("src")
        self.source_job = None

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for item in list(self._items):
            await self._release_item(item)
        self._items.clear()

    async def renew_held(self) -> None:
        for item in list(self._items):
            try:
                await item["client"].disk_slot("renew", item["job"])
            except Exception:
                log.warning("disk renew %s failed", item["job"])

    async def _release_role(self, role: str) -> None:
        for item in list(self._items):
            if item["role"] != role:
                continue
            await self._release_item(item)
            self._items.remove(item)

    async def _release_item(self, item: dict) -> None:
        try:
            await item["client"].disk_slot("release", item["job"])
        except Exception:
            log.warning("disk release %s failed", item["job"])

    async def _renew(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                await self.renew_held()


@dataclass
class MigrationSlots:
    ssd_source: str | None
    ssd_target: str | None
    hold: DiskHold


def _clients(pool, source: str, target: str):
    fn = getattr(pool, "client_for", None)
    if fn is None:
        return None, None
    try:
        return fn(str(source)), fn(str(target))
    except Exception:
        return None, None


async def _acquire_one(
    client,
    job: str,
    torrent_id: int | None,
    hold: DiskHold,
    still: StillQueued,
    torrent: int,
    engine_id: str,
) -> str:
    """running — слот взят. fallback — очереди нет. cancelled — задачу сняли."""
    announced = False
    while True:
        if not await still():
            # Задача уже могла встать в очередь движка. Снимаем её, иначе
            # слот потом стартует копирование, которое отменили.
            try:
                await client.disk_slot("release", job)
            except Exception:
                log.warning("migrate %s: disk release %s on cancel failed", torrent, engine_id)
            return "cancelled"
        try:
            state = await client.disk_slot(
                "acquire",
                job,
                kind="copy",
                torrent_id=torrent_id,
                leased=True,
            )
        except Exception:
            log.warning("migrate %s: disk slot %s unavailable", torrent, engine_id)
            return "fallback"
        if state is None:
            return "fallback"
        if state == "running":
            return "running"
        if not announced:
            log.info("migrate %s: waiting disk slot on %s", torrent, engine_id)
            announced = True
        await hold.renew_held()
        await asyncio.sleep(0.4)


async def open_migration_slots(
    pool,
    source: str,
    target: str,
    torrent_id: int,
    still: StillQueued,
) -> MigrationSlots | None:
    """Занять дисковые слоты. None — задачу отменили, пока она ждала.

    При исключении уже взятые слоты отпускаются. Вызывающий должен закрыть hold,
    если результат не None.
    """
    source = str(source)
    target = str(target)
    src_client, dst_client = _clients(pool, source, target)
    hold = DiskHold()
    if src_client is None or dst_client is None:
        return MigrationSlots(source, target, hold)

    try:
        src_kind = await disk_kind_of(src_client)
        dst_kind = src_kind if source == target else await disk_kind_of(dst_client)
        ssd_source: str | None = None if uses_disk_queue(src_kind) else source
        ssd_target: str | None = None if uses_disk_queue(dst_kind) else target
        sides: list[tuple[str, object, str, int | None, str]] = []
        if uses_disk_queue(src_kind) and source != target:
            sides.append(("src", src_client, f"m{torrent_id}-src", None, source))
        if uses_disk_queue(dst_kind):
            sides.append(("dst", dst_client, f"m{torrent_id}-dst", torrent_id, target))
        sides.sort(key=lambda item: item[4])
        for role, client, job, tid, eng in sides:
            outcome = await _acquire_one(client, job, tid, hold, still, torrent_id, eng)
            if outcome == "cancelled":
                await hold.close()
                return None
            if outcome == "fallback":
                if role == "src":
                    ssd_source = source
                else:
                    ssd_target = target
                continue
            hold.add(role=role, client=client, job=job)
        hold.start_renew()
        return MigrationSlots(ssd_source, ssd_target, hold)
    except Exception:
        await hold.close()
        raise
