import importlib

from fastapi.testclient import TestClient


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/auth.sqlite3")
    monkeypatch.setenv("ENGINE_URL", "http://engine.test:8081")
    monkeypatch.setenv("SEEDING_AUTO_SCHEMA", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("SEEDING_API_KEYS", raising=False)
    import seeding_api.main as main

    importlib.reload(main)
    return main


def _login(client, username, password="secret1"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_me_exposes_login_and_session_expiry(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        client.post(
            "/api/v1/auth/users",
            json={"username": "alice", "password": "secret1", "role": "operator"},
        )
        token = _login(client, "alice")
        me = client.get("/api/v1/auth/me", headers={"X-API-Key": token})
        assert me.status_code == 200
        body = me.json()
        assert body["name"] == "alice"
        assert body["source"] == "session"
        assert body["last_login_at"]
        assert body["expires_at"]


def test_sessions_revoke_other_keeps_current(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        client.post(
            "/api/v1/auth/users",
            json={"username": "alice", "password": "secret1", "role": "viewer"},
        )
        a = _login(client, "alice")
        b = _login(client, "alice")
        listed = client.get("/api/v1/auth/me/sessions", headers={"X-API-Key": a})
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 2
        current = next(r for r in rows if r["current"])
        other = next(r for r in rows if not r["current"])

        refuse = client.delete(
            f"/api/v1/auth/me/sessions/{current['id']}", headers={"X-API-Key": a}
        )
        assert refuse.status_code == 400

        gone = client.delete(
            f"/api/v1/auth/me/sessions/{other['id']}", headers={"X-API-Key": a}
        )
        assert gone.status_code == 200
        assert client.get("/api/v1/auth/me", headers={"X-API-Key": b}).status_code == 401
        assert client.get("/api/v1/auth/me", headers={"X-API-Key": a}).status_code == 200

        c = _login(client, "alice")
        client.post("/api/v1/auth/me/sessions/revoke-others", headers={"X-API-Key": a})
        assert client.get("/api/v1/auth/me", headers={"X-API-Key": c}).status_code == 401
        assert client.get("/api/v1/auth/me", headers={"X-API-Key": a}).status_code == 200


def test_api_key_cannot_list_sessions(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        client.post(
            "/api/v1/auth/users",
            json={"username": "root", "password": "rootpass", "role": "admin"},
        )
        token = _login(client, "root", "rootpass")
        key = client.post(
            "/api/v1/auth/keys",
            headers={"X-API-Key": token},
            json={"name": "bot", "role": "admin"},
        ).json()["key"]
        r = client.get("/api/v1/auth/me/sessions", headers={"X-API-Key": key})
        assert r.status_code == 400


def test_my_audit_is_own_rows_only(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        client.post(
            "/api/v1/auth/users",
            json={"username": "bob", "password": "secret1", "role": "admin"},
        )
        bob = _login(client, "bob")
        created = client.post(
            "/api/v1/auth/users",
            headers={"X-API-Key": bob},
            json={"username": "alice", "password": "secret1", "role": "operator"},
        )
        assert created.status_code == 201
        alice = _login(client, "alice")
        client.put(
            "/api/v1/auth/me/avatar",
            headers={"X-API-Key": alice},
            json={"avatar": "aurora"},
        )
        mine = client.get("/api/v1/auth/me/audit", headers={"X-API-Key": alice})
        assert mine.status_code == 200
        actors = {row["actor"] for row in mine.json()}
        assert actors <= {"alice"}
        assert any(row["summary"] == "Сменён аватар" for row in mine.json())

        forbidden = client.get("/api/v1/audit", headers={"X-API-Key": alice})
        assert forbidden.status_code == 403

        admin_log = client.get("/api/v1/audit", headers={"X-API-Key": bob})
        assert admin_log.status_code == 200
        assert any(row["actor"] == "alice" for row in admin_log.json())
