"""API download ticket + features flag."""

from __future__ import annotations

import importlib
import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

ENGINE = "http://engine.test:8081"


@pytest.fixture
def api_download(monkeypatch, tmp_path):
    db = tmp_path / "api.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_DATA_ROOT", "/data")
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.setenv("SEEDING_DOWNLOAD_ENABLED", "1")
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    monkeypatch.setenv("SEEDING_UPLOAD_PER_ENGINE", "1")
    monkeypatch.setenv(
        "SEEDING_UPLOAD_BASE_URLS",
        '{"*":"https://seedbox2.hw-s.ru/u/b"}',
    )
    monkeypatch.setenv(
        "SEEDING_UPLOAD_RELAY_BASE_URLS",
        '{"*":"https://185-185-143-207.sslip.io/u/b"}',
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    return main


def _wire(mock: respx.MockRouter) -> None:
    mock.get(f"{ENGINE}/health").mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "service": "engine", "backend": "mock"},
        ),
    )

    def on_register(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "db_id": body["db_id"],
                "magnet_uri": body.get("magnet_uri"),
                "save_path": body["save_path"],
                "runtime_status": "active",
                "info_hash": None,
                "progress": None,
                "lt_state": None,
            },
        )

    mock.post(f"{ENGINE}/internal/v1/torrents").mock(side_effect=on_register)
    safe = re.escape(ENGINE)
    mock.get(url__regex=safe + r"/internal/v1/torrents/\d+/files$").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "index": 0,
                    "path": "Season 1/e01.mkv",
                    "size": 1234567890,
                    "downloaded": 1234567890,
                    "progress": 1.0,
                    "priority": 4,
                }
            ],
        )
    )


def test_download_features_and_ticket(api_download):
    with respx.mock(assert_all_called=False) as mock:
        _wire(mock)
        with TestClient(api_download.app) as client:
            feat = client.get("/api/v1/download/features")
            assert feat.status_code == 200
            assert feat.json()["enabled"] is True
            assert feat.json()["relay_enabled"] is True

            created = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "N",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert created.status_code == 201, created.text
            tid = created.json()["id"]
            engine_id = created.json()["engine_id"]

            ticket = client.post(
                "/api/v1/download/ticket",
                json={"torrent_id": tid, "path": "Season 1/e01.mkv"},
            )
            assert ticket.status_code == 200, ticket.text
            body = ticket.json()
            assert body["filename"] == "e01.mkv"
            assert body["size"] == 1234567890
            assert body["url"] == (
                f"https://seedbox2.hw-s.ru/u/b/{engine_id}/download/v1/file"
            )
            assert body["relay_url"] == (
                f"https://185-185-143-207.sslip.io/u/b/{engine_id}/download/v1/file"
            )
            assert body["ticket"]


def test_download_ticket_rejects_escape(api_download):
    with respx.mock(assert_all_called=False) as mock:
        _wire(mock)
        with TestClient(api_download.app) as client:
            created = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "N",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            tid = created.json()["id"]
            bad = client.post(
                "/api/v1/download/ticket",
                json={"torrent_id": tid, "path": "../secrets"},
            )
            assert bad.status_code == 422


def test_download_disabled_404(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/d.db")
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.setenv("SEEDING_DOWNLOAD_ENABLED", "0")
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    monkeypatch.setenv("SEEDING_UPLOAD_BASE_URLS", '{"*":"https://x/u/b"}')
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        feat = client.get("/api/v1/download/features")
        assert feat.json()["enabled"] is False
        r = client.post("/api/v1/download/ticket", json={"torrent_id": 1, "path": "a.bin"})
        assert r.status_code == 404
