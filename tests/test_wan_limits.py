import importlib
import json

import pytest

from seeding_api.audit import summarize

B1 = "http://192.168.1.171:8081"
B2 = "http://192.168.1.171:8082"
A1 = "http://192.168.2.243:8081"


def test_audit_summarizes_wan_limits():
    assert summarize("POST", "/api/v1/network/links/wan1/limits") == "Изменены лимиты канала wan1"
    assert summarize("POST", "/api/v1/session/limits") == "Изменены глобальные лимиты"


@pytest.fixture
def wan_api(monkeypatch, tmp_path):
    pytest.importorskip("arq")
    pytest.importorskip("respx")
    pytest.importorskip("aiosqlite")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/wan.sqlite3")
    monkeypatch.setenv(
        "ENGINES_CONFIG",
        json.dumps(
            [
                {"id": "b1", "url": B1, "storage_prefix": "/data/b1"},
                {"id": "b2", "url": B2, "storage_prefix": "/data/b2"},
                {"id": "a1", "url": A1, "storage_prefix": "/data/a1"},
            ]
        ),
    )
    monkeypatch.delenv("ENGINE_URL", raising=False)
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.setenv("SEEDING_RUNTIME_SNAPSHOT_INTERVAL", "0")
    monkeypatch.setenv("SEEDING_ENGINE_REFRESH_INTERVAL", "0")
    monkeypatch.delenv("REDIS_URL", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    return main


def test_wan_limits_stamp_only_that_uplink(wan_api):
    httpx = pytest.importorskip("httpx")
    respx = pytest.importorskip("respx")
    from fastapi.testclient import TestClient

    called: list[str] = []

    def _session_ok():
        return httpx.Response(
            200,
            json={
                "torrents": 0,
                "download_rate": 0,
                "upload_rate": 0,
                "total_uploaded": 0,
                "total_downloaded": 0,
            },
        )

    with respx.mock(assert_all_called=False) as mock:
        for url in (B1, B2, A1):
            mock.get(f"{url}/internal/v1/session/stats").mock(return_value=_session_ok())
            mock.post(f"{url}/internal/v1/session/limits").mock(
                side_effect=lambda request, u=url: (
                    called.append(u),
                    httpx.Response(200, json={"ok": True}),
                )[1]
            )
        with TestClient(wan_api.app) as client:
            missing = client.post(
                "/api/v1/network/links/wan9/limits",
                json={"download_limit": 1024, "upload_limit": 2048},
            )
            assert missing.status_code == 404

            r = client.post(
                "/api/v1/network/links/wan1/limits",
                json={"download_limit": 10 * 1024, "upload_limit": 20 * 1024},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["id"] == "wan1"
            assert body["engines"] == ["b1", "b2"]
            assert body["saved"] == 2
            assert body["applied"] == 2
            assert set(called) == {B1, B2}

            engines = {e["id"]: e for e in client.get("/api/v1/engines").json()}
            assert engines["b1"]["download_limit"] == 10 * 1024
            assert engines["b1"]["upload_limit"] == 20 * 1024
            assert engines["b2"]["upload_limit"] == 20 * 1024
            assert engines["a1"]["download_limit"] is None
            assert engines["a1"]["upload_limit"] is None
