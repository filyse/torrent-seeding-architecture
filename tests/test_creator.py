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
    monkeypatch.setenv("SEEDING_STORAGE_KIND", "ssd")
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


def test_engine_list_tasks_empty(engine_app):
    with TestClient(engine_app) as client:
        r = client.get("/internal/v1/creator/tasks")
        assert r.status_code == 200, r.text
        assert r.json() == []


def test_engine_list_tasks_reports_created(engine_app):
    with TestClient(engine_app) as client:
        svc = engine_app.state.creator
        from seeding_engine.creator import CreateStatus, CreateTask

        svc._tasks[0] = CreateTask(id=0, source_path="b1/Show", name="Show")
        svc._tasks[1] = CreateTask(
            id=1, source_path="b1/movie.mp4", name="movie", status=CreateStatus.COMPLETED
        )
        r = client.get("/internal/v1/creator/tasks")
        assert r.status_code == 200, r.text
        ids = [t["id"] for t in r.json()]
        assert ids == [1, 0]  # свежие сверху


def test_engine_delete_removes_task(engine_app):
    with TestClient(engine_app) as client:
        svc = engine_app.state.creator
        from seeding_engine.creator import CreateStatus, CreateTask

        svc._tasks[0] = CreateTask(
            id=0, source_path="b1/movie.mp4", name="movie", status=CreateStatus.COMPLETED
        )
        r = client.delete("/internal/v1/creator/tasks/0")
        assert r.status_code == 200, r.text
        assert client.get("/internal/v1/creator/tasks").json() == []
        # повторное удаление — уже нет
        assert client.delete("/internal/v1/creator/tasks/0").status_code == 404


def test_creator_service_ttl_prunes_on_access(tmp_path):
    import time

    from seeding_engine.creator import CreateStatus, CreateTask, CreatorService

    captured: list[tuple[str, list]] = []
    svc = CreatorService(
        task_ttl=1, on_deleted=lambda reason, tasks: captured.append((reason, tasks))
    )
    try:
        old = CreateTask(
            id=0, source_path="b1/x", name="x", status=CreateStatus.COMPLETED
        )
        old.created_at = time.time() - 5
        svc._tasks[0] = old
        assert svc.list_all() == []  # прунится при обращении
        assert svc.get(0) is None
        assert captured
        assert captured[0][0] == "ttl"
        assert captured[0][1][0]["id"] == 0
        assert captured[0][1][0]["source_path"] == "b1/x"
    finally:
        svc.shutdown()


def test_creator_releases_hold_on_error(monkeypatch, tmp_path):
    import time

    from seeding_engine.creator import CreatorService

    events: list[bool] = []
    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    (tmp_path / "f.bin").write_bytes(b"x")
    monkeypatch.setattr("seeding_engine.creator._try_import_libtorrent", lambda: object())
    svc = CreatorService(on_hash_hold=lambda on: events.append(on) or on)
    monkeypatch.setattr(
        svc,
        "_build_torrent",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("hash fail")),
    )
    try:
        task = svc.create("f.bin", skip_episode_check=True)
        deadline = time.time() + 5
        while time.time() < deadline:
            row = svc.get(task.id)
            if row and row.status.value == "failed":
                break
            time.sleep(0.05)
        assert events == [True, False]
        done = svc.get(task.id)
        assert done is not None
        assert done.upload_hold is False
    finally:
        svc.shutdown()


def test_creator_service_manual_delete_does_not_notify():
    """Ручной delete() молчит: Kafka публикует оркестратор, иначе deadlock."""
    from seeding_engine.creator import CreateStatus, CreateTask, CreatorService

    captured: list = []
    svc = CreatorService(on_deleted=lambda reason, tasks: captured.append(tasks))
    try:
        svc._tasks[0] = CreateTask(
            id=0, source_path="b1/x", name="x", status=CreateStatus.COMPLETED
        )
        assert svc.delete(0) is True
        assert captured == []
    finally:
        svc.shutdown()


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


def test_creator_list_tasks_aggregates(api_module):
    task = {
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
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.get(f"{ENGINE}/internal/v1/creator/tasks").mock(
            return_value=httpx.Response(200, json=[task])
        )
        with TestClient(api_module.app) as client:
            r = client.get("/api/v1/creator/tasks")
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body) == 1
            assert body[0]["engine_id"] == "default"
            assert body[0]["id"] == 0


def test_creator_delete_task_proxies(api_module, monkeypatch):
    published: list[tuple] = []

    async def _fake_publish(engine_id, task_id, **kwargs):
        published.append((engine_id, task_id, kwargs.get("reason")))
        return True

    monkeypatch.setattr(
        "seeding_api.routers.creator.publish_creator_deleted_async", _fake_publish
    )
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        route = mock.delete(f"{ENGINE}/internal/v1/creator/tasks/0").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        with TestClient(api_module.app) as client:
            r = client.delete("/api/v1/creator/tasks/default/0")
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
            assert route.called
            assert published == [("default", 0, "deleted")]


def test_creator_deleted_event_from_engine(api_module, monkeypatch):
    monkeypatch.setenv("SEEDING_ENGINE_REGISTER_KEY", "test-register")
    published: list[tuple] = []

    async def _fake_publish(engine_id, task_id, **kwargs):
        published.append((engine_id, task_id, kwargs.get("reason"), kwargs.get("name")))
        return True

    monkeypatch.setattr(
        "seeding_api.routers.creator.publish_creator_deleted_async", _fake_publish
    )
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        with TestClient(api_module.app) as client:
            r = client.post(
                "/api/v1/creator/events/deleted",
                json={
                    "engine_id": "a1",
                    "tasks": [
                        {
                            "id": 3,
                            "name": "Show",
                            "source_path": "a1/Show",
                            "reason": "ttl",
                        }
                    ],
                },
                headers={"X-Register-Key": "test-register"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["published"] == 1
            assert published == [("a1", 3, "ttl", "Show")]
            denied = client.post(
                "/api/v1/creator/events/deleted",
                json={"engine_id": "a1", "tasks": [{"id": 1}]},
            )
            assert denied.status_code == 401


def test_creator_deleted_payload_has_composite_key():
    from seeding_api.kafka_notify import CREATOR_DELETED_EVENT, creator_deleted_payload

    payload = creator_deleted_payload("A1", 0, reason="ttl", name="Show")
    assert payload["event"] == CREATOR_DELETED_EVENT
    assert payload["task_key"] == "a1:0"
    assert payload["engine_id"] == "a1"
    assert payload["reason"] == "ttl"


def test_creator_delete_task_not_found(api_module):
    with respx.mock(assert_all_called=False) as mock:
        _wire_health(mock)
        mock.delete(f"{ENGINE}/internal/v1/creator/tasks/9").mock(
            return_value=httpx.Response(404, json={"detail": "task not found"})
        )
        with TestClient(api_module.app) as client:
            r = client.delete("/api/v1/creator/tasks/default/9")
            assert r.status_code == 404


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
