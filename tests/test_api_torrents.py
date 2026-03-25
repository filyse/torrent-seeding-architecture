import importlib
import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

ENGINE = "http://engine.test:8081"


@pytest.fixture
def api_module(monkeypatch, tmp_path):
    db = tmp_path / "api.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    return main


def _wire_engine_mocks(mock: respx.MockRouter) -> None:
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
    mock.post(url__regex=safe + r"/internal/v1/torrents/\d+/pause").mock(
        side_effect=lambda r: httpx.Response(
            200,
            json={
                "db_id": int(r.url.path.rstrip("/").split("/")[-2]),
                "magnet_uri": "magnet:x",
                "save_path": "/tmp",
                "runtime_status": "paused",
                "info_hash": None,
                "progress": None,
                "lt_state": None,
            },
        )
    )
    mock.post(url__regex=safe + r"/internal/v1/torrents/\d+/resume").mock(
        side_effect=lambda r: httpx.Response(
            200,
            json={
                "db_id": int(r.url.path.rstrip("/").split("/")[-2]),
                "magnet_uri": "magnet:x",
                "save_path": "/tmp",
                "runtime_status": "active",
                "info_hash": None,
                "progress": None,
                "lt_state": None,
            },
        )
    )
    mock.get(url__regex=safe + r"/internal/v1/torrents/\d+$").mock(
        return_value=httpx.Response(
            200,
            json={
                "db_id": 1,
                "magnet_uri": "magnet:x",
                "save_path": "/tmp",
                "runtime_status": "active",
                "info_hash": None,
                "progress": None,
                "lt_state": None,
            },
        )
    )
    mock.delete(url__regex=safe + r"/internal/v1/torrents/\d+$").mock(
        return_value=httpx.Response(204),
    )


def test_health_degraded_without_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/h.db")
    monkeypatch.setenv("ENGINE_URL", "http://127.0.0.1:59999")
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["checks"]["database"] is True
        assert body["checks"]["engine"] is False
        assert body["status"] == "degraded"


def test_create_torrent_happy_path(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            r = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "N",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert r.status_code == 201, r.text
            data = r.json()
            assert data["display_name"] == "N"
            assert data["status"] == "downloading"


def test_http_error_shape(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            r = client.post(
                "/api/v1/torrents",
                json={"display_name": "", "save_path": "/x", "magnet_uri": "not-a-magnet"},
            )
            assert r.status_code == 422
            err = r.json()["error"]
            assert err["code"] == 422


def test_jobs_noop_503_without_redis(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            r = client.post("/api/v1/jobs/noop")
            assert r.status_code == 503
            assert "queue unavailable" in r.json()["error"]["message"]


def test_delete_torrent_happy_path(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            c = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "D",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert c.status_code == 201
            tid = c.json()["id"]
            d = client.delete(f"/api/v1/torrents/{tid}")
            assert d.status_code == 204, d.text
            g = client.get(f"/api/v1/torrents/{tid}")
            assert g.status_code == 404


def test_delete_removes_db_when_engine_unreachable_by_default(api_module, monkeypatch):
    monkeypatch.delenv("SEEDING_REQUIRE_ENGINE_FOR_DELETE", raising=False)
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            c = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "X",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert c.status_code == 201
            tid = c.json()["id"]

            async def broken_remove(db_id: int) -> bool:
                raise httpx.ConnectError(
                    "refused",
                    request=httpx.Request(
                        "DELETE",
                        f"{ENGINE}/internal/v1/torrents/{db_id}",
                    ),
                )

            api_module.app.state.engine_client.remove_from_runtime = (  # type: ignore[method-assign]
                broken_remove
            )

            d = client.delete(f"/api/v1/torrents/{tid}")
            assert d.status_code == 204, d.text
            g = client.get(f"/api/v1/torrents/{tid}")
            assert g.status_code == 404


def test_delete_502_when_require_engine_for_delete(api_module, monkeypatch):
    monkeypatch.setenv("SEEDING_REQUIRE_ENGINE_FOR_DELETE", "1")
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            c = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "Y",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert c.status_code == 201
            tid = c.json()["id"]

            async def broken_remove(db_id: int) -> bool:
                raise httpx.ConnectError(
                    "refused",
                    request=httpx.Request(
                        "DELETE",
                        f"{ENGINE}/internal/v1/torrents/{db_id}",
                    ),
                )

            api_module.app.state.engine_client.remove_from_runtime = (  # type: ignore[method-assign]
                broken_remove
            )

            d = client.delete(f"/api/v1/torrents/{tid}")
            assert d.status_code == 502, d.text
            g = client.get(f"/api/v1/torrents/{tid}")
            assert g.status_code == 200


def test_pause_returns_502_when_engine_unreachable(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            c = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "P",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert c.status_code == 201
            tid = c.json()["id"]

            async def broken_pause(db_id: int) -> dict:
                raise httpx.ConnectError(
                    "refused",
                    request=httpx.Request(
                        "POST",
                        f"{ENGINE}/internal/v1/torrents/{db_id}/pause",
                    ),
                )

            api_module.app.state.engine_client.pause = broken_pause  # type: ignore[method-assign]

            r = client.post(f"/api/v1/torrents/{tid}/pause")
            assert r.status_code == 502, r.text
            err = r.json()["error"]
            assert err["code"] == 502


def test_resume_returns_502_when_engine_unreachable(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(api_module.app) as client:
            c = client.post(
                "/api/v1/torrents",
                json={
                    "display_name": "R",
                    "save_path": "/data",
                    "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )
            assert c.status_code == 201
            tid = c.json()["id"]

            async def broken_resume(db_id: int) -> dict:
                raise httpx.ConnectError(
                    "refused",
                    request=httpx.Request(
                        "POST",
                        f"{ENGINE}/internal/v1/torrents/{db_id}/resume",
                    ),
                )

            api_module.app.state.engine_client.resume = broken_resume  # type: ignore[method-assign]

            r = client.post(f"/api/v1/torrents/{tid}/resume")
            assert r.status_code == 502, r.text


def test_api_key_required_for_torrents(monkeypatch, tmp_path):
    db = tmp_path / "k.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.setenv("SEEDING_API_KEYS", "secret-one,secret-two")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    with respx.mock(assert_all_called=False) as mock:
        _wire_engine_mocks(mock)
        with TestClient(main.app) as client:
            assert client.get("/api/v1/torrents").status_code == 401
            assert (
                client.get("/api/v1/torrents", headers={"X-API-Key": "secret-one"}).status_code == 200
            )
