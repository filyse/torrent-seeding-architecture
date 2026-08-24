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


def test_change_own_password_keeps_current_session(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/auth/users",
            json={"username": "alice", "password": "oldpass", "role": "operator"},
        )
        assert created.status_code == 201

        a = client.post("/api/v1/auth/login", json={"username": "alice", "password": "oldpass"})
        b = client.post("/api/v1/auth/login", json={"username": "alice", "password": "oldpass"})
        token_a = a.json()["token"]
        token_b = b.json()["token"]
        headers = {"X-API-Key": token_a}

        wrong = client.put(
            "/api/v1/auth/me/password",
            headers=headers,
            json={"current_password": "nope", "password": "newpass"},
        )
        assert wrong.status_code == 400

        ok = client.put(
            "/api/v1/auth/me/password",
            headers=headers,
            json={"current_password": "oldpass", "password": "newpass"},
        )
        assert ok.status_code == 200

        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["name"] == "alice"

        other = client.get("/api/v1/auth/me", headers={"X-API-Key": token_b})
        assert other.status_code == 401

        assert client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "oldpass"}
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "newpass"}
        ).status_code == 200


def test_api_key_cannot_change_password(monkeypatch, tmp_path):
    main = _app(monkeypatch, tmp_path)
    with TestClient(main.app) as client:
        client.post(
            "/api/v1/auth/users",
            json={"username": "root", "password": "rootpass", "role": "admin"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"username": "root", "password": "rootpass"}
        ).json()["token"]
        key = client.post(
            "/api/v1/auth/keys",
            headers={"X-API-Key": token},
            json={"name": "bot", "role": "admin"},
        ).json()["key"]

        r = client.put(
            "/api/v1/auth/me/password",
            headers={"X-API-Key": key},
            json={"current_password": "x", "password": "newpass"},
        )
        assert r.status_code == 400
