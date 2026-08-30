"""GET /download/v1/file: ticket, path-escape, Range."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upload"))
sys.path.insert(0, str(ROOT / "engine"))

from seeding_engine.download_http import build_download_router  # noqa: E402
from seeding_upload.ticket import issue_download_ticket  # noqa: E402


class _Runtime:
    def __init__(self, root: Path):
        self.root = root

    async def content_file_path(self, db_id: int, rel: str) -> Path | None:
        if db_id != 7:
            return None
        target = (self.root / rel).resolve()
        base = self.root.resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"unsafe path: {rel}")
        return target


def _app(tmp_path: Path) -> tuple[TestClient, Path]:
    root = tmp_path / "content"
    nested = root / "Season 1"
    nested.mkdir(parents=True)
    blob = nested / "e01.mkv"
    blob.write_bytes(b"ABCDEFGHIJ")
    app = FastAPI()
    app.state.torrent_runtime = _Runtime(root)
    app.include_router(build_download_router(lambda: "sec", expected_engine_id="b6"))
    return TestClient(app), blob


def _token(**kwargs) -> str:
    params = {
        "secret": "sec",
        "engine_id": "b6",
        "torrent_id": 7,
        "path": "Season 1/e01.mkv",
        "uid": "alice",
        "ttl_seconds": 300,
    }
    params.update(kwargs)
    return issue_download_ticket(**params)


def test_download_full_file(tmp_path: Path):
    client, _ = _app(tmp_path)
    res = client.get("/download/v1/file", params={"ticket": _token()})
    assert res.status_code == 200, res.text
    assert res.content == b"ABCDEFGHIJ"
    assert "attachment" in res.headers.get("content-disposition", "")
    assert res.headers.get("accept-ranges") == "bytes"


def test_download_range(tmp_path: Path):
    client, _ = _app(tmp_path)
    res = client.get(
        "/download/v1/file",
        params={"ticket": _token()},
        headers={"Range": "bytes=2-5"},
    )
    assert res.status_code == 206
    assert res.content == b"CDEF"
    assert res.headers["content-range"] == "bytes 2-5/10"


def test_download_rejects_other_engine(tmp_path: Path):
    client, _ = _app(tmp_path)
    res = client.get("/download/v1/file", params={"ticket": _token(engine_id="b1")})
    assert res.status_code == 403


def test_download_rejects_path_escape(tmp_path: Path):
    client, _ = _app(tmp_path)
    secret = "sec"
    # ticket с нормальным путём, query пытается выйти
    res = client.get(
        "/download/v1/file",
        params={"ticket": _token(), "path": "../secrets"},
    )
    assert res.status_code == 400


def test_download_expired_ticket(tmp_path: Path):
    import time

    from seeding_upload import ticket as tmod

    client, _ = _app(tmp_path)
    token = tmod.issue_download_ticket(
        secret="sec",
        engine_id="b6",
        torrent_id=7,
        path="Season 1/e01.mkv",
        uid="alice",
        ttl_seconds=1,
        now=time.time() - 10,
    )
    res = client.get("/download/v1/file", params={"ticket": token})
    assert res.status_code == 403
    assert "expired" in res.json()["detail"]


def test_rel_under_content_root_strips_torrent_name():
    from seeding_engine.torrent_runtime import LibtorrentTorrentRuntime

    base = Path("/data/b1/Show.s01")
    assert (
        LibtorrentTorrentRuntime.rel_under_content_root(base, "Show.s01/e01.mkv")
        == "e01.mkv"
    )
    assert LibtorrentTorrentRuntime.rel_under_content_root(base, "e01.mkv") == "e01.mkv"


def test_download_missing_file(tmp_path: Path):
    client, _ = _app(tmp_path)
    res = client.get(
        "/download/v1/file",
        params={"ticket": _token(path="Season 1/missing.mkv")},
    )
    assert res.status_code == 404
