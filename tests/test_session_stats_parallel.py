"""session/stats всех движков: один медленный движок не держит остальных (2026-10-28)."""

import asyncio
import time

import httpx
from seeding_api.engine_pool import EnginePool
from seeding_db.engine_registry import EngineSpec


class _Client:
    def __init__(self, delay: float = 0.0, fail: bool = False, payload: dict | None = None):
        self.delay = delay
        self.fail = fail
        self.payload = payload or {"torrents": 1, "upload_rate": 10}

    async def session_stats(self) -> dict:
        await asyncio.sleep(self.delay)
        if self.fail:
            raise httpx.ConnectTimeout("boom")
        return dict(self.payload)

    async def aclose(self) -> None:
        return None


def _pool(clients: dict[str, _Client]) -> EnginePool:
    specs = [EngineSpec(id=eid, url=f"http://{eid}:8081", storage_prefix=f"/{eid}") for eid in clients]
    pool = EnginePool(specs=specs)
    pool._by_id = dict(clients)  # noqa: SLF001
    return pool


async def test_parallel_with_deadline_does_not_wait_for_slow_engine():
    pool = _pool({"a1": _Client(), "b1": _Client(), "c1": _Client(delay=5.0)})
    t = time.perf_counter()
    out = await pool.session_stats_all(deadline=0.3)
    assert time.perf_counter() - t < 1.5
    assert list(out) == ["a1", "b1", "c1"]
    assert out["a1"]["torrents"] == 1
    assert out["c1"] == {"error": True}


async def test_stale_last_known_value_when_engine_times_out():
    slow = _Client(payload={"torrents": 7, "upload_rate": 99})
    pool = _pool({"a1": _Client(), "c1": slow})
    first = await pool.session_stats_all(deadline=1.0, stale_ok=True)
    assert first["c1"]["torrents"] == 7 and not first["c1"].get("stale")
    slow.delay = 5.0
    second = await pool.session_stats_all(deadline=0.2, stale_ok=True)
    assert second["c1"]["stale"] is True
    assert second["c1"]["torrents"] == 7
    agg = EnginePool.aggregate_session_stats(second)
    assert agg["engines_ok"] == 2 and agg["engines_stale"] == 1


async def test_no_stale_without_flag_and_http_error_is_error():
    c1 = _Client()
    pool = _pool({"c1": c1})
    await pool.session_stats_all()
    c1.fail = True
    assert (await pool.session_stats_all())["c1"] == {"error": True}
    assert (await pool.session_stats_all(stale_ok=True))["c1"]["stale"] is True
