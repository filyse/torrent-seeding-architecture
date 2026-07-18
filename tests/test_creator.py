import importlib

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

ENGINE = "http://engine.test:8081"


# ---------------------------------------------------------------------------
# Engine-side: browse + creator service
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_app(monkeypatch, tmp_path):
    (tmp_path / "b1" / "Show.S01").mkdir(parents=True)
    (tmp_path / "b1" / "Show.S01" / "ep01.mkv").write_bytes(b"a" * 1024)
    (tmp_path / "b1" / "movie.mp4").write_bytes(b"b" * 2048)
    monkeypatch.setenv("SEEDING_ENGINE_BACKEND", "mock")
    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    import seeding_engine.main as main

    importlib.reload(main)
    return main.app


def test_engine_browse_lists_data_root(engine_app):
    with TestClient(engine_app) as client:
        r = client.get("/internal/v1/fs/browse")
        assert r.status_code == 200, r.text
        names = [i["name"] for i in r.json()]
        assert "b1" in names


def test_engine_browse_into_subdir(engine_app):
    with TestClient(engine_app) as client:
        r = client.get("/internal/v1/fs/browse", params={"path": "b1"})
        assert r.status_code == 200, r.text
        items = {i["name"]: i for i in r.json()}
        assert items["Show.S01"]["is_dir"] is True
        assert items["movie.mp4"]["is_dir"] is False


def test_engine_browse_rejects_traversal(engine_app):
    with TestClient(engine_app) as client:
        r = client.get("/internal/v1/fs/browse", params={"path": "../.."})
        assert r.status_code == 400


def test_engine_browse_missing_dir(engine_app):
    with TestClient(engine_app) as client:
        r = client.get("/internal/v1/fs/browse", params={"path": "nope"})
        assert r.status_code == 404


def test_engine_create_without_libtorrent_returns_501(engine_app, monkeypatch):
    import seeding_engine.creator as creator

    monkeypatch.setattr(creator, "_try_import_libtorrent", lambda: None)
    with TestClient(engine_app) as client:
        r = client.post(
            "/internal/v1/creator/tasks",
            json={"source_path": "b1/Show.S01", "skip_episode_check": True},
        )
        assert r.status_code == 501


def test_creator_service_builds_v1_torrent(monkeypatch, tmp_path):
    lt = pytest.importorskip("libtorrent")
    import time

    from seeding_engine.creator import CreatorService

    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    (tmp_path / "b1").mkdir()
    (tmp_path / "b1" / "file.bin").write_bytes(b"x" * (1024 * 1024))

    svc = CreatorService()
    try:
        task = svc.create("b1/file.bin", skip_episode_check=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            t = svc.get(task.id)
            assert t is not None
            if t.status.value in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.2)
        t = svc.get(task.id)
        assert t is not None
        assert t.status.value == "completed", t.error
        assert t.torrent_bytes is not None
        decoded = lt.bdecode(t.torrent_bytes)
        assert b"info" in decoded
        assert t.save_path == str(tmp_path / "b1")
    finally:
        svc.shutdown()


# ---------------------------------------------------------------------------
# Orchestrator-side: /api/v1/creator router
# ---------------------------------------------------------------------------


@pytest.fixture
def api_module(monkeypatch, tmp_path):
    db = tmp_path / "creator.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    monkeypatch.setenv("SEEDING_DATA_ROOT", "/data")
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("SEEDING_API_KEYS", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    return main


def _wire_health(mock: respx.MockRouter) -> None:
    mock.get(f"{ENGINE}/health").mock(
        return_value=httpx.Response(
            200, json={"status": "ok", "service": "engine", "backend": "mock"}
        )
    )


def test_creator_browse_proxy(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.get(f"{ENGINE}/internal/v1/fs/browse").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "b1", "path": "b1", "is_dir": True, "size": 0, "modified": 1.0},
                ],
            )
        )
        with TestClient(api_module.app) as client:
            r = client.get("/api/v1/creator/browse", params={"engine_id": "default", "path": ""})
            assert r.status_code == 200, r.text
            assert r.json()[0]["name"] == "b1"


def test_creator_browse_unknown_engine(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        with TestClient(api_module.app) as client:
            r = client.get("/api/v1/creator/browse", params={"engine_id": "nope"})
            assert r.status_code == 404


def test_creator_create_and_status(api_module):
    task = {
        "id": 0,
        "source_path": "b1/Show",
        "save_path": "/data/b1",
        "status": "queued",
        "progress": 0,
        "message": "В очереди",
        "error": None,
        "name": "Show",
        "file_count": 3,
        "created_at": 1.0,
        "updated_at": 1.0,
        "has_torrent": False,
    }
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.post(f"{ENGINE}/internal/v1/creator/tasks").mock(
            return_value=httpx.Response(200, json=task)
        )
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/0").mock(
            return_value=httpx.Response(
                200, json={**task, "status": "completed", "progress": 100, "has_torrent": True}
            ),
        )
        with TestClient(api_module.app) as client:
            r = client.post(
                "/api/v1/creator/tasks",
                json={"engine_id": "default", "source_path": "b1/Show", "skip_episode_check": True},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["engine_id"] == "default"
            assert body["id"] == 0

            s = client.get("/api/v1/creator/tasks/default/0")
            assert s.status_code == 200, s.text
            assert s.json()["status"] == "completed"


def test_creator_seed_registers_torrent(api_module):
    completed = {
        "id": 0,
        "source_path": "b1/Show",
        "save_path": "/data/b1",
        "status": "completed",
        "progress": 100,
        "message": "Готово",
        "error": None,
        "name": "Show",
        "file_count": 3,
        "created_at": 1.0,
        "updated_at": 2.0,
        "has_torrent": True,
    }
    torrent_bytes = b"d4:infod4:name4:testee"
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/0").mock(
            return_value=httpx.Response(200, json=completed)
        )
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/0/torrent").mock(
            return_value=httpx.Response(
                200, content=torrent_bytes, headers={"content-type": "application/x-bittorrent"}
            )
        )

        def on_register(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "db_id": body["db_id"],
                    "magnet_uri": None,
                    "save_path": body["save_path"],
                    "runtime_status": "active",
                    "info_hash": None,
                    "progress": None,
                    "lt_state": None,
                },
            )

        mock.post(f"{ENGINE}/internal/v1/torrents").mock(side_effect=on_register)

        with TestClient(api_module.app) as client:
            r = client.post(
                "/api/v1/creator/tasks/default/0/seed",
                json={"label": "auto", "display_name": ""},
            )
            assert r.status_code == 201, r.text
            data = r.json()
            assert data["save_path"] == "/data/b1"
            assert data["display_name"] == "Show"
            assert data["status"] == "downloading"
            assert data["label"] == "auto"


def test_creator_seed_rejects_incomplete_task(api_module):
    queued = {
        "id": 5,
        "source_path": "b1/Show",
        "save_path": "/data/b1",
        "status": "processing",
        "progress": 40,
        "message": "Хеширование",
        "error": None,
        "name": "Show",
        "file_count": 3,
        "created_at": 1.0,
        "updated_at": 2.0,
        "has_torrent": False,
    }
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/5").mock(
            return_value=httpx.Response(200, json=queued)
        )
        with TestClient(api_module.app) as client:
            r = client.post("/api/v1/creator/tasks/default/5/seed", json={})
            assert r.status_code == 409


def test_creator_download_streams_bytes(api_module):
    completed = {
        "id": 0,
        "source_path": "b1/Show",
        "save_path": "/data/b1",
        "status": "completed",
        "progress": 100,
        "message": "Готово",
        "error": None,
        "name": "Show",
        "file_count": 3,
        "created_at": 1.0,
        "updated_at": 2.0,
        "has_torrent": True,
    }
    torrent_bytes = b"d4:infod4:name4:testee"
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/0").mock(
            return_value=httpx.Response(200, json=completed)
        )
        mock.get(f"{ENGINE}/internal/v1/creator/tasks/0/torrent").mock(
            return_value=httpx.Response(200, content=torrent_bytes)
        )
        with TestClient(api_module.app) as client:
            r = client.get("/api/v1/creator/tasks/default/0/download")
            assert r.status_code == 200, r.text
            assert r.content == torrent_bytes
            assert "attachment" in r.headers["content-disposition"]
