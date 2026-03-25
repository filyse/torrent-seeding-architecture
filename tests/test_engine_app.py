import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def engine_app_mock(monkeypatch):
    monkeypatch.setenv("SEEDING_ENGINE_BACKEND", "mock")
    import seeding_engine.main as main

    importlib.reload(main)
    return main.app


def test_engine_health_reports_mock_backend(engine_app_mock):
    with TestClient(engine_app_mock) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "engine"
        assert body["backend"] == "mock"


def test_engine_internal_register_mock(engine_app_mock):
    with TestClient(engine_app_mock) as client:
        r = client.post(
            "/internal/v1/torrents",
            json={
                "db_id": 1,
                "magnet_uri": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "save_path": "/tmp/seeding-test",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["db_id"] == 1
        assert data["runtime_status"] == "active"


def test_engine_internal_delete_mock(engine_app_mock):
    with TestClient(engine_app_mock) as client:
        client.post(
            "/internal/v1/torrents",
            json={
                "db_id": 7,
                "magnet_uri": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "save_path": "/tmp/seeding-test",
            },
        )
        d = client.delete("/internal/v1/torrents/7")
        assert d.status_code == 204
        g = client.get("/internal/v1/torrents/7")
        assert g.status_code == 404
