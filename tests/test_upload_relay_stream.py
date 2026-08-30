"""GET-стрим релея: Range/206, без буфера всего файла на пути заливки."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from seeding_upload_relay.main import app, uses_stream


def test_uses_stream_only_for_get_head():
    assert uses_stream("GET") is True
    assert uses_stream("HEAD") is True
    assert uses_stream("PUT") is False
    assert uses_stream("POST") is False


@respx.mock
def test_relay_get_range_forwarded(monkeypatch):
    import seeding_upload_relay.main as main

    monkeypatch.setattr(
        main,
        "UPSTREAMS",
        {"b": "https://seedbox2.example/u/b"},
    )
    respx.get("https://seedbox2.example/u/b/b6/download/v1/file").mock(
        return_value=httpx.Response(
            206,
            content=b"ab",
            headers={
                "Content-Range": "bytes 0-1/10",
                "Content-Length": "2",
                "Accept-Ranges": "bytes",
                "Content-Type": "application/octet-stream",
            },
        )
    )
    with TestClient(app) as client:
        r = client.get(
            "/u/b/b6/download/v1/file?ticket=x",
            headers={"Range": "bytes=0-1"},
        )
    assert r.status_code == 206
    assert r.content == b"ab"
    assert r.headers["content-range"] == "bytes 0-1/10"
    req = respx.calls[0].request
    assert req.headers.get("range") == "bytes=0-1"
