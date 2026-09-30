"""Перенос не считает ожидание слота хеша неполной копией."""

import asyncio

import pytest
from seeding_api.migrate import _wait_until_checked


class _Snaps:
    def __init__(self, snaps: list[dict]) -> None:
        self.snaps = snaps
        self.calls = 0

    async def runtime_snapshot(self, db_id: int) -> dict:
        self.calls += 1
        if self.calls <= len(self.snaps):
            return self.snaps[self.calls - 1]
        return self.snaps[-1]


@pytest.mark.asyncio
async def test_wait_does_not_finish_before_check_starts(monkeypatch):
    async def instant(_interval):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)
    client = _Snaps([{"progress": 0.4, "lt_state": "downloading"}])
    snap = await _wait_until_checked(client, 1, 40)
    assert client.calls > 35
    assert snap is not None
    assert snap["progress"] == 0.4


@pytest.mark.asyncio
async def test_wait_accepts_incomplete_only_after_a_check(monkeypatch):
    async def instant(_interval):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)
    client = _Snaps(
        [{"progress": 0.2, "lt_state": "checking_files"}]
        + [{"progress": 0.4, "lt_state": "downloading"}] * 20
    )
    snap = await _wait_until_checked(client, 1, 40)
    assert client.calls < 20
    assert snap is not None
    assert snap["progress"] == 0.4
