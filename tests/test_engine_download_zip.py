"""GET /download/v1/zip: ZIP всей раздачи (STORE, ZIP64, UTF-8, Content-Length)."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upload"))
sys.path.insert(0, str(ROOT / "engine"))

from seeding_engine.download_http import build_download_router  # noqa: E402
from seeding_engine.zipstream import ZipEntry, ZipPlan  # noqa: E402
from seeding_upload.ticket import issue_download_ticket  # noqa: E402


class _Runtime:
    def __init__(self, root: Path, files: list[dict]):
        self.root = root
        self.files = files

    async def list_files(self, db_id: int):
        return self.files if db_id == 7 else []

    async def content_file_path(self, db_id: int, rel: str):
        target = (self.root / rel).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("unsafe")
        return target


def _setup(tmp_path: Path):
    root = tmp_path / "data"
    (root / "Сериал" / "Сезон 1").mkdir(parents=True)
    a = root / "Сериал" / "Сезон 1" / "серия 01.mkv"
    a.write_bytes(b"A" * 5000)
    b = root / "Сериал" / "info.nfo"
    b.write_bytes(b"hello")
    c = root / "Сериал" / "partial.mkv"
    c.write_bytes(b"\0" * 100)
    files = [
        {"path": "Сериал/Сезон 1/серия 01.mkv", "size": 5000, "downloaded": 5000},
        {"path": "Сериал/info.nfo", "size": 5, "downloaded": 5},
        {"path": "Сериал/partial.mkv", "size": 100, "downloaded": 50},
        {"path": "Сериал/missing.mkv", "size": 10, "downloaded": 10},
    ]
    app = FastAPI()
    app.state.torrent_runtime = _Runtime(root, files)
    app.include_router(build_download_router(lambda: "sec", expected_engine_id="b6"))
    return TestClient(app)


def _tok(kind="dz", **kw):
    p = dict(secret="sec", engine_id="b6", torrent_id=7, path="x", uid="u", ttl_seconds=300, kind=kind)
    p.update(kw)
    return issue_download_ticket(**p)


def test_zip_ok(tmp_path: Path):
    client = _setup(tmp_path)
    res = client.get("/download/v1/zip", params={"ticket": _tok(), "name": "Сериал: «1»"})
    assert res.status_code == 200, res.text
    assert int(res.headers["content-length"]) == len(res.content)
    cd = res.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd and "%D0%A1" in cd and cd.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert z.testzip() is None
    assert z.namelist() == ["Сериал/Сезон 1/серия 01.mkv", "Сериал/info.nfo"]
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    assert z.read("Сериал/info.nfo") == b"hello"


def test_zip_head_length_matches(tmp_path: Path):
    client = _setup(tmp_path)
    h = client.head("/download/v1/zip", params={"ticket": _tok()})
    g = client.get("/download/v1/zip", params={"ticket": _tok()})
    assert h.status_code == 200 and h.headers["content-length"] == str(len(g.content))


def test_zip_rejects_file_ticket(tmp_path: Path):
    client = _setup(tmp_path)
    res = client.get("/download/v1/zip", params={"ticket": _tok(kind="dl", path="Сериал/info.nfo")})
    assert res.status_code == 403


def test_file_rejects_zip_ticket(tmp_path: Path):
    client = _setup(tmp_path)
    res = client.get("/download/v1/file", params={"ticket": _tok()})
    assert res.status_code == 403


def test_zip_other_engine_and_missing(tmp_path: Path):
    client = _setup(tmp_path)
    assert client.get("/download/v1/zip", params={"ticket": _tok(engine_id="a1")}).status_code == 403
    assert client.get("/download/v1/zip").status_code == 401
    assert client.get("/download/v1/zip", params={"ticket": _tok(torrent_id=8)}).status_code == 404


def test_zip_unzip_cli(tmp_path: Path):
    if not shutil.which("unzip"):
        pytest.skip("no unzip")
    client = _setup(tmp_path)
    out = tmp_path / "o.zip"
    out.write_bytes(client.get("/download/v1/zip", params={"ticket": _tok()}).content)
    r = subprocess.run(["unzip", "-t", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not os.getenv("ZIP64_BIG_TEST"), reason="ZIP64_BIG_TEST=1 (пишет ~4.4 ГБ)")
def test_zip64_sparse_big(tmp_path: Path):
    big = tmp_path / "большой.bin"
    with open(big, "wb") as f:
        f.truncate(4500 * 1024 * 1024)
        f.seek(4500 * 1024 * 1024 - 4)
        f.write(b"tail")
    small = tmp_path / "после.txt"
    small.write_bytes("привет".encode())
    ents = [ZipEntry(name=f"R/{p.name}", size=p.stat().st_size, mtime=0, path=p) for p in (big, small)]
    plan = ZipPlan(ents)

    async def rd(e):
        with open(e.path, "rb") as f:
            while c := f.read(1 << 20):
                yield c

    out = tmp_path / "big.zip"

    async def run():
        with open(out, "wb") as o:
            async for b in plan.stream(rd):
                o.write(b)

    asyncio.run(run())
    assert out.stat().st_size == plan.total
    z = zipfile.ZipFile(out)
    assert z.read("R/после.txt").decode() == "привет"
    with z.open("R/большой.bin") as f:
        f.seek(4500 * 1024 * 1024 - 4)
        assert f.read() == b"tail"


def test_zip_slots_released(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEEDING_DOWNLOAD_ZIP_PARALLEL", "1")
    client = _setup(tmp_path)
    for _ in range(3):
        assert client.get("/download/v1/zip", params={"ticket": _tok()}).status_code == 200
    with client.stream("GET", "/download/v1/zip", params={"ticket": _tok()}) as r:
        next(r.iter_bytes())  # обрыв посреди потока
    assert client.get("/download/v1/zip", params={"ticket": _tok()}).status_code == 200
