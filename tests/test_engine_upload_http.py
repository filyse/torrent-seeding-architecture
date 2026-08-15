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
