"""Тесты WebSocket-хаба (Фаза 7, WS-1)."""

import asyncio
import json

import pytest
from seeding_api.ws_hub import WsClient, WsHub, ws_enabled


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, s: str) -> None:
        self.sent.append(s)


async def _drain(client: WsClient) -> None:
    """Прогнать writer один раз и остановить."""
    task = asyncio.create_task(client.run_writer())
    await asyncio.sleep(0.01)
    client.closed = True
    client.wake()
    await task


@pytest.mark.asyncio
async def test_publish_reaches_subscriber_and_coalesces():
    hub = WsHub()
    ws = FakeWS()
    client = WsClient(ws, principal=None)
    await hub.register(client)
    await hub.subscribe(client, "stats")

    # Две публикации до запуска writer — коалесинг оставит только последнюю по каналу.
    await hub.publish("stats", {"n": 1})
    await hub.publish("stats", {"n": 2})
    await _drain(client)

    assert len(ws.sent) == 1
    msg = json.loads(ws.sent[0])
    assert msg["channel"] == "stats"
    assert msg["data"] == {"n": 2}
    assert msg["type"] == "event"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    hub = WsHub()
    client = WsClient(FakeWS(), principal=None)
    await hub.register(client)
    await hub.subscribe(client, "torrent:5")
    assert hub.has_subscribers("torrent:5")
    await hub.unsubscribe(client, "torrent:5")
    assert not hub.has_subscribers("torrent:5")
    n = await hub.publish("torrent:5", {"x": 1})
    assert n == 0


@pytest.mark.asyncio
async def test_unregister_clears_channels():
    hub = WsHub()
    client = WsClient(FakeWS(), principal=None)
    await hub.register(client)
    await hub.subscribe(client, "stats")
    await hub.subscribe(client, "engines")
    await hub.unregister(client)
    assert hub.metrics["clients"] == 0
    assert not hub.has_subscribers("stats")
    assert not hub.has_subscribers("engines")


@pytest.mark.asyncio
async def test_publish_sync_matches_subscribers():
    hub = WsHub()
    client = WsClient(FakeWS(), principal=None)
    await hub.register(client)
    await hub.subscribe(client, "migrate:7")
    n = hub.publish_sync("migrate:7", {"phase": "copying"})
    assert n == 1
    n0 = hub.publish_sync("migrate:999", {"phase": "x"})
    assert n0 == 0


@pytest.mark.asyncio
async def test_any_subscribers_prefix():
    hub = WsHub()
    client = WsClient(FakeWS(), principal=None)
    await hub.register(client)
    await hub.subscribe(client, "torrent:42")
    assert hub.any_subscribers("torrent:")
    assert not hub.any_subscribers("job:")


def test_ws_enabled_flag(monkeypatch):
    monkeypatch.delenv("SEEDING_WS_ENABLED", raising=False)
    assert ws_enabled() is False
    monkeypatch.setenv("SEEDING_WS_ENABLED", "1")
    assert ws_enabled() is True
    monkeypatch.setenv("SEEDING_WS_ENABLED", "true")
    assert ws_enabled() is True
    monkeypatch.setenv("SEEDING_WS_ENABLED", "0")
    assert ws_enabled() is False
