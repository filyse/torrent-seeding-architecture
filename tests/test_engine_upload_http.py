"""Вшитый /upload/v1: ticket только своего движка, чанки → файл."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upload"))

from seeding_upload.router import build_upload_router  # noqa: E402
from seeding_upload.storage import UploadStorage  # noqa: E402
from seeding_upload.ticket import issue_ticket  # noqa: E402


def _app(tmp_path: Path) -> tuple[TestClient, Path]:
    root = tmp_path / "b1"
    dest = root / "inbox"
    dest.mkdir(parents=True)
    storage = UploadStorage({"b1": root}, chunk_size=4, gc_minutes=30)
    app = FastAPI()
    app.include_router(
        build_upload_router(storage, lambda: "sec", expected_engine_id="b1")
    )
    return TestClient(app), dest


def test_embedded_upload_complete(tmp_path: Path):
    client, dest = _app(tmp_path)
    token = issue_ticket(
        secret="sec",
        engine_id="b1",
        dest_dir=str(dest),
        filename="x.bin",
        size=4,
        uid="alice",
    )
    headers = {"X-Upload-Ticket": token}
    created = client.post("/upload/v1/uploads", json={"overwrite": False}, headers=headers)
    assert created.status_code == 200, created.text
    uid = created.json()["id"]
    put = client.put(
        f"/upload/v1/uploads/{uid}/chunks/0",
        content=b"abcd",
        headers=headers,
    )
    assert put.status_code == 200, put.text
    done = client.post(f"/upload/v1/uploads/{uid}/complete", headers=headers)
    assert done.status_code == 200, done.text
    assert (dest / "x.bin").read_bytes() == b"abcd"


def test_hdd_complete_returns_202_until_assemble_finishes(tmp_path: Path):
    client, dest = _app(tmp_path)

    class Runtime:
        def __init__(self) -> None:
            self._disk = object()
            self.info: dict | None = None

        def assemble_state(self, _upload_id: str) -> dict | None:
            return self.info

        async def assemble_upload(self, _storage, sess) -> str:
            self.info = {
                "assemble": "queued",
                "path": "",
                "size": sess.size,
                "jti": sess.jti,
            }
            return "queued"

        async def cancel_assemble(self, _upload_id: str) -> None:
            if self.info is not None:
                self.info = {**self.info, "assemble": "cancelled"}

    runtime = Runtime()
    client.app.state.torrent_runtime = runtime
    token = issue_ticket(
        secret="sec",
        engine_id="b1",
        dest_dir=str(dest),
        filename="x.bin",
        size=4,
        uid="alice",
    )
    headers = {"X-Upload-Ticket": token}
    created = client.post("/upload/v1/uploads", json={"overwrite": False}, headers=headers)
    uid = created.json()["id"]
    client.put(f"/upload/v1/uploads/{uid}/chunks/0", content=b"abcd", headers=headers)
    done = client.post(f"/upload/v1/uploads/{uid}/complete", headers=headers)
    assert done.status_code == 202, done.text
    assert done.json()["state"] == "queued"
    assert not (dest / "x.bin").exists()
    waiting = client.get(f"/upload/v1/uploads/{uid}", headers=headers)
    assert waiting.json()["assemble"] == "queued"
    runtime.info = {
        "assemble": "done",
        "path": str(dest / "x.bin"),
        "size": 4,
        "jti": runtime.info["jti"],
    }
    finished = client.get(f"/upload/v1/uploads/{uid}", headers=headers)
    assert finished.json()["assemble"] == "done"
    assert finished.json()["path"] == str(dest / "x.bin")


def test_embedded_rejects_other_engine_ticket(tmp_path: Path):
    client, dest = _app(tmp_path)
    token = issue_ticket(
        secret="sec",
        engine_id="b2",
        dest_dir=str(dest),
        filename="y.bin",
        size=4,
        uid="alice",
    )
    res = client.post(
        "/upload/v1/uploads",
        json={"overwrite": False},
        headers={"X-Upload-Ticket": token},
    )
    assert res.status_code == 403
