"""Ждущий слот перенос виден как «В очереди на перенос» и переживает рестарт API."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from seeding_api import migrate as mig
from seeding_db.models import Base, MigrationJob, TorrentStatus
from seeding_db.repository import MigrationRepository, TorrentRepository
from seeding_db.session import create_engine, create_session_factory


@pytest.fixture
async def sf(tmp_path):
    eng = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'mq.sqlite3'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(eng)
    await eng.dispose()


async def _mk_torrents(sf, n: int, engine: str) -> list[int]:
    ids = []
    async with sf() as s:
        repo = TorrentRepository(s)
        for i in range(n):
            row = await repo.create(
                display_name=f"t{i}", save_path=f"/data/{engine}",
                magnet_uri=None, status=TorrentStatus.seeding.value,
            )
            row.engine_id = engine
            ids.append(row.id)
        await s.commit()
    return ids


class _Pool:
    def spec(self, engine_id):
        return SimpleNamespace(url=f"https://{engine_id}:8081")


def _kw(tid: int, src: str, dst: str) -> dict:
    return dict(
        torrent_id=tid, source_engine_id=src, target_engine_id=dst,
        source_save_path=f"/data/{src}", target_save_path=f"/data/{dst}",
        src_content_path="", display_name=f"t{tid}", transport="http",
        source_url=f"https://{src}:8081",
    )


async def _status(sf, tid):
    async with sf() as s:
        row = await TorrentRepository(s).get_by_id(tid)
        job = await MigrationRepository(s).get(tid)
        pos = await MigrationRepository(s).queue_position(job) if job and job.state == "queued" else None
        return row.status, (job.state if job else None), pos


async def _settle():
    # aiosqlite ходит в поток: даём задачам дойти до ожидания слота/ворот
    await asyncio.sleep(0.3)


@pytest.fixture
def fake_run(monkeypatch):
    gates: dict[int, asyncio.Event] = {}
    started: list[int] = []

    async def run(session_factory, pool, *, torrent_id, **_kw):
        async with session_factory() as s:
            await TorrentRepository(s).update_status(torrent_id, TorrentStatus.migrating.value)
            await MigrationRepository(s).set_state(torrent_id, "running", phase="copying")
            await s.commit()
        started.append(torrent_id)
        gates.setdefault(torrent_id, asyncio.Event())
        await gates[torrent_id].wait()
        async with session_factory() as s:
            await TorrentRepository(s).update_status(torrent_id, TorrentStatus.seeding.value)
            await MigrationRepository(s).delete(torrent_id)
            await s.commit()

    async def one(_app):
        return 1

    monkeypatch.setattr(mig, "run_migration", run)
    monkeypatch.setattr(mig, "_migrate_limit", one)
    return gates, started


@pytest.mark.asyncio
async def test_several_transfers_show_queue_and_positions(sf, fake_run):
    gates, started = fake_run
    app = SimpleNamespace(state=SimpleNamespace(session_factory=sf))
    ids = await _mk_torrents(sf, 3, "qa1")
    for tid in ids:
        mig.launch_migration(app, _Pool(), resume=False, **_kw(tid, "qa1", "qb1"))
        await _settle()
    assert started == [ids[0]]
    assert await _status(sf, ids[0]) == ("migrating", "running", None)
    assert await _status(sf, ids[1]) == ("migrate_queued", "queued", 1)
    assert await _status(sf, ids[2]) == ("migrate_queued", "queued", 2)
    # API отдаёт место и цель
    gates[ids[0]] = gates.get(ids[0]) or asyncio.Event()
    gates[ids[0]].set()
    await _settle()
    assert started == [ids[0], ids[1]]
    assert await _status(sf, ids[0]) == ("seeding", None, None)
    assert await _status(sf, ids[2]) == ("migrate_queued", "queued", 1)
    for tid in ids[1:]:
        gates.setdefault(tid, asyncio.Event()).set()
        await _settle()
    await asyncio.gather(*list(app.state.migrate_tasks))


@pytest.mark.asyncio
async def test_cancel_while_queued_never_copies(sf, fake_run):
    gates, started = fake_run
    app = SimpleNamespace(state=SimpleNamespace(session_factory=sf))
    a, b = await _mk_torrents(sf, 2, "qa2")
    mig.launch_migration(app, _Pool(), resume=False, **_kw(a, "qa2", "qb2"))
    await _settle()
    mig.launch_migration(app, _Pool(), resume=False, **_kw(b, "qa2", "qb2"))
    await _settle()
    assert await mig.cancel_migration(sf, _Pool(), torrent_id=b) is True
    assert await _status(sf, b) == ("seeding", None, None)
    gates.setdefault(a, asyncio.Event()).set()
    await asyncio.gather(*list(app.state.migrate_tasks))
    assert started == [a]


@pytest.mark.asyncio
async def test_restart_recovers_running_and_queued_and_drops_stale_failed(sf, fake_run):
    gates, started = fake_run
    run_id, q1, q2, stale, real_fail = await _mk_torrents(sf, 5, "qa3")
    async with sf() as s:
        repo = TorrentRepository(s)
        mrepo = MigrationRepository(s)
        for tid, state in ((run_id, "running"), (q1, "queued"), (q2, "queued")):
            await mrepo.upsert(tid, **{k: v for k, v in _kw(tid, "qa3", "qb3").items()
                                       if k not in ("torrent_id", "source_url")}, state=state)
            await repo.update_status(
                tid, "migrating" if state == "running" else "migrate_queued"
            )
        # уже на цели — старая ошибка не нужна
        await mrepo.upsert(stale, **{k: v for k, v in _kw(stale, "qa9", "qa3").items()
                                     if k not in ("torrent_id", "source_url")}, state="failed")
        # реально прерван — остаётся возобновляемым
        await mrepo.upsert(real_fail, **{k: v for k, v in _kw(real_fail, "qa3", "qb9").items()
                                         if k not in ("torrent_id", "source_url")}, state="failed")
        await s.commit()
    app = SimpleNamespace(state=SimpleNamespace(session_factory=sf))
    n = await mig.recover_orphaned_migrations(app, _Pool())
    assert n == 3
    await _settle()
    assert started == [run_id]
    assert (await _status(sf, q1))[:2] == ("migrate_queued", "queued")
    async with sf() as s:
        assert await s.get(MigrationJob, stale) is None
        assert (await s.get(MigrationJob, real_fail)).state == "failed"
        facets = await TorrentRepository(s).facets()
    assert facets["states"]["migrate_queued"] == 2
    assert facets["states"]["migrating"] == 3
    for tid in (run_id, q1, q2):
        gates.setdefault(tid, asyncio.Event()).set()
        await _settle()
    await asyncio.gather(*list(app.state.migrate_tasks))
    assert started[0] == run_id and sorted(started[1:]) == sorted([q1, q2])
