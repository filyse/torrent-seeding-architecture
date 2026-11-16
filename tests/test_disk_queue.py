"""Общая дисковая очередь HDD: слоты, приоритет, аренда, встречные копии."""

from __future__ import annotations

import asyncio

import pytest
from seeding_api.disk_gate import open_migration_slots, uses_disk_queue
from seeding_engine.disk_queue import DiskQueue


def test_uses_disk_queue_skips_ssd_only():
    assert uses_disk_queue("ssd") is False
    assert uses_disk_queue("hdd") is True
    assert uses_disk_queue("unknown") is True
    assert uses_disk_queue(None) is True


@pytest.mark.asyncio
async def test_fifth_hdd_job_waits_until_a_slot_frees():
    queue = DiskQueue(4)
    for i in range(4):
        assert await queue.acquire(f"j{i}", band="user", kind="copy") == "running"
    assert await queue.acquire("j4", band="user", kind="copy") == "queued"
    await queue.release("j0")
    assert await queue.poll("j4") == "running"
    await queue.release("missing")
    await queue.release("j4")
    assert await queue.poll("j4") == "missing"


@pytest.mark.asyncio
async def test_user_job_starts_before_a_waiting_check():
    queue = DiskQueue(1)
    order: list[str] = []
    gate = asyncio.Event()

    async def check_running() -> None:
        order.append("check")
        await gate.wait()

    async def user() -> None:
        order.append("user")

    async def check_next() -> None:
        order.append("next")

    assert await queue.acquire("c1", band="check", kind="recheck", on_start=check_running) == "running"
    await asyncio.sleep(0)
    assert await queue.acquire("c2", band="check", kind="recheck", on_start=check_next) == "queued"
    assert await queue.acquire("u", band="user", kind="copy", on_start=user) == "queued"
    assert order == ["check"]
    gate.set()
    for _ in range(20):
        if order == ["check", "user", "next"]:
            break
        await asyncio.sleep(0.02)
    assert order == ["check", "user", "next"]


@pytest.mark.asyncio
async def test_lower_limit_does_not_stop_running_jobs():
    queue = DiskQueue(2)
    assert await queue.acquire("a", band="user", kind="copy") == "running"
    assert await queue.acquire("b", band="user", kind="copy") == "running"
    assert await queue.set_limit(1) == 1
    assert await queue.poll("a") == "running"
    assert await queue.poll("b") == "running"
    assert await queue.acquire("c", band="user", kind="copy") == "queued"


@pytest.mark.asyncio
async def test_same_torrent_does_not_take_a_second_slot():
    queue = DiskQueue(4)
    assert await queue.acquire("copy-7", band="user", kind="copy", torrent_id=7) == "running"
    assert await queue.acquire("check-7", band="check", kind="recheck", torrent_id=7) == "running"
    assert await queue.holds_torrent(7) is True
    assert await queue.poll("check-7") == "missing"
    assert queue.blocks_checks() == 1
    await queue.release("copy-7")
    assert await queue.holds_torrent(7) is False


@pytest.mark.asyncio
async def test_leased_slot_expires_and_renew_keeps_it():
    short = DiskQueue(1, ttl=0.05)
    assert await short.acquire("a", band="user", kind="copy", leased=True) == "running"
    await asyncio.sleep(0.08)
    assert await short.expire() == 1
    assert await short.poll("a") == "missing"

    kept = DiskQueue(1, ttl=0.1)
    assert await kept.acquire("b", band="user", kind="copy", leased=True) == "running"
    await asyncio.sleep(0.06)
    assert await kept.renew("b") == "running"
    await asyncio.sleep(0.06)
    assert await kept.expire() == 0
    assert await kept.poll("b") == "running"

    owned = DiskQueue(1, ttl=0.05)
    assert await owned.acquire("c", band="user", kind="copy", leased=False) == "running"
    await asyncio.sleep(0.08)
    assert await owned.expire() == 0


@pytest.mark.asyncio
async def test_waiting_user_reserves_the_slot_against_checks():
    queue = DiskQueue(2, admit_running=lambda: 2)
    assert await queue.acquire("u", band="user", kind="copy") == "queued"
    assert queue.blocks_checks() == 1


class _Client:
    def __init__(self, kind: str, queue: DiskQueue | None) -> None:
        self.kind = kind
        self.queue = queue

    async def session_stats(self) -> dict:
        return {"disk_kind": self.kind}

    async def disk_slot(self, action: str, job_id: str, **kw) -> str | None:
        if self.queue is None:
            return None
        if action == "acquire":
            return await self.queue.acquire(
                job_id,
                band="user",
                kind=kw.get("kind") or "copy",
                torrent_id=kw.get("torrent_id"),
                leased=bool(kw.get("leased", True)),
            )
        if action == "renew":
            return await self.queue.renew(job_id)
        if action == "release":
            await self.queue.release(job_id)
            return "released"
        return await self.queue.poll(job_id)


class _Pool:
    def __init__(self, clients: dict[str, _Client]) -> None:
        self._clients = clients

    def client_for(self, engine_id: str) -> _Client:
        return self._clients[engine_id]


@pytest.mark.asyncio
async def test_opposite_hdd_copies_do_not_deadlock():
    pool = _Pool({
        "b1": _Client("hdd", DiskQueue(1)),
        "b2": _Client("hdd", DiskQueue(1)),
    })

    async def still() -> bool:
        return True

    async def copy(src: str, dst: str, torrent_id: int) -> None:
        opened = await open_migration_slots(pool, src, dst, torrent_id, still)
        assert opened is not None
        await asyncio.sleep(0.05)
        await opened.hold.release_source()
        await opened.hold.close()

    await asyncio.wait_for(
        asyncio.gather(copy("b1", "b2", 1), copy("b2", "b1", 2)),
        timeout=3,
    )


@pytest.mark.asyncio
async def test_ssd_side_skips_disk_queue_and_old_engine_falls_back():
    pool = _Pool({
        "a1": _Client("ssd", None),
        "b1": _Client("hdd", DiskQueue(1)),
    })

    async def still() -> bool:
        return True

    opened = await open_migration_slots(pool, "a1", "b1", 3, still)
    assert opened is not None
    assert opened.ssd_source == "a1"
    assert opened.ssd_target is None
    assert opened.hold.dest_job == "m3-dst"
    assert opened.hold.source_job is None
    await opened.hold.close()

    old = _Pool({"b1": _Client("hdd", None), "b2": _Client("hdd", None)})
    fallback = await open_migration_slots(old, "b1", "b2", 4, still)
    assert fallback is not None
    assert fallback.ssd_source == "b1"
    assert fallback.ssd_target == "b2"
    assert fallback.hold.source_job is None
    assert fallback.hold.dest_job is None
    await fallback.hold.close()


@pytest.mark.asyncio
async def test_cancel_while_waiting_drops_the_queued_disk_job():
    disk = DiskQueue(1)
    assert await disk.acquire("busy", band="user", kind="copy") == "running"
    pool = _Pool({"b1": _Client("hdd", disk), "b2": _Client("hdd", DiskQueue(1))})
    allow = True

    async def still() -> bool:
        return allow

    async def stop() -> None:
        nonlocal allow
        await asyncio.sleep(0.15)
        allow = False

    stopper = asyncio.create_task(stop())
    opened = await open_migration_slots(pool, "b1", "b2", 9, still)
    await stopper
    assert opened is None
    assert await disk.poll("m9-src") == "missing"


@pytest.mark.asyncio
async def test_assemble_is_queued_until_the_slot_then_running():
    from types import SimpleNamespace

    from seeding_engine.torrent_runtime import LibtorrentTorrentRuntime

    rt = LibtorrentTorrentRuntime.__new__(LibtorrentTorrentRuntime)
    rt._assemble = {}
    rt._disk = DiskQueue(1)
    gate = asyncio.Event()

    async def hold() -> None:
        await gate.wait()

    assert await rt._disk.acquire("busy", band="user", kind="copy", on_start=hold) == "running"
    sess = SimpleNamespace(id="up1", size=4, jti="jti")
    seen: dict[str, str] = {}

    class Storage:
        def complete(self, _sess: object) -> str:
            seen["mid"] = str(rt.assemble_state("up1")["assemble"])
            return "/data/b1/file"

    assert await rt.assemble_upload(Storage(), sess) == "queued"
    assert rt.assemble_state("up1")["assemble"] == "queued"
    gate.set()
    for _ in range(50):
        if rt.assemble_state("up1")["assemble"] == "done":
            break
        await asyncio.sleep(0.05)
    assert seen["mid"] == "running"
    assert rt.assemble_state("up1") == {
        "assemble": "done",
        "path": "/data/b1/file",
        "size": 4,
        "jti": "jti",
    }
