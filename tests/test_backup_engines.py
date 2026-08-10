"""Суточный бэкап состояния движков и признак «копии протухли».

Мотивация тестов: cron с бэкапом БД однажды молча умер на 23 дня, и заметить это было
неоткуда. Поэтому проверяем не только сбор архивов, но и то, что протухание видно.
"""

import os
import tarfile
import time
from pathlib import Path

import pytest
from seeding_api import backup_engines
from seeding_api.routers import backups as backups_router


@pytest.fixture
def backup_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDING_BACKUP_DIR", str(tmp_path))
    return tmp_path


def _touch_archive(root: Path, name: str, age_hours: float = 0.0, size: int = 10) -> Path:
    d = root / "engines"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_bytes(b"x" * size)
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
    return path


def test_retention_removes_only_old_archives(backup_root, monkeypatch):
    monkeypatch.setenv("SEEDING_BACKUP_RETENTION_DAYS", "14")
    _touch_archive(backup_root, "b1-20260810-040000.tar.gz", age_hours=1)
    _touch_archive(backup_root, "b2-20260726-040000.tar.gz", age_hours=15 * 24)
    _touch_archive(backup_root, "a1-20260809-040000.tar.gz", age_hours=13 * 24)

    removed = backup_engines.prune(backup_engines.backup_dir(), backup_engines.retention_days())

    assert removed == 1
    left = sorted(p.name for p in (backup_root / "engines").glob("*.tar.gz"))
    assert left == ["a1-20260809-040000.tar.gz", "b1-20260810-040000.tar.gz"]


def test_retention_ignores_foreign_files(backup_root, monkeypatch):
    monkeypatch.setenv("SEEDING_BACKUP_RETENTION_DAYS", "1")
    d = backup_root / "engines"
    d.mkdir(parents=True)
    stray = d / "readme.txt"
    stray.write_text("не трогать")
    old = time.time() - 30 * 86400
    os.utime(stray, (old, old))

    backup_engines.prune(d, 1)

    assert stray.exists()


def test_retention_days_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SEEDING_BACKUP_RETENTION_DAYS", "не число")
    assert backup_engines.retention_days() == 14
    monkeypatch.setenv("SEEDING_BACKUP_RETENTION_DAYS", "0")
    assert backup_engines.retention_days() == 1


def test_engine_meta_summary_reports_freshness(backup_root):
    _touch_archive(backup_root, "b1-20260810-040000.tar.gz", age_hours=2, size=100)
    _touch_archive(backup_root, "b1-20260809-040000.tar.gz", age_hours=26, size=100)
    _touch_archive(backup_root, "a1-20260810-040000.tar.gz", age_hours=3, size=50)

    summary = backups_router._engine_meta_summary()

    assert summary["available"] is True
    assert summary["size"] == 250
    # По движку берём САМЫЙ СВЕЖИЙ архив, иначе вчерашняя копия выглядела бы протухшей.
    assert [e["engine_id"] for e in summary["engines"]] == ["a1", "b1"]
    assert summary["stale"] is False
    assert all(e["stale"] is False for e in summary["engines"])


def test_engine_meta_summary_flags_stale(backup_root, monkeypatch):
    monkeypatch.setenv("SEEDING_BACKUP_STALE_HOURS", "48")
    _touch_archive(backup_root, "b1-20260701-040000.tar.gz", age_hours=20 * 24)

    summary = backups_router._engine_meta_summary()

    assert summary["stale"] is True
    assert summary["engines"][0]["stale"] is True


def test_engine_meta_summary_without_dir_is_not_fatal(backup_root):
    summary = backups_router._engine_meta_summary()
    assert summary["available"] is False
    assert summary["engines"] == []
    # Отсутствие архивов — это тоже повод показать предупреждение, а не «всё хорошо».
    assert summary["stale"] is True


def test_empty_dir_counts_as_stale(backup_root):
    (backup_root / "engines").mkdir()
    summary = backups_router._engine_meta_summary()
    assert summary["available"] is True
    assert summary["stale"] is True


@pytest.mark.asyncio
async def test_dump_engine_writes_archive_and_cleans_partial(backup_root, monkeypatch):
    """Успешная выгрузка кладёт готовый файл и не оставляет .partial."""
    payload = b"tar-gz-bytes"

    class _Resp:
        async def aiter_bytes(self, _n):
            yield payload[:4]
            yield payload[4:]

    class _Client:
        def stream_meta_archive(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield _Resp(), 7

            return cm()

    class _Pool:
        def client_for(self, _eid):
            return _Client()

    dest = backup_root / "engines"
    dest.mkdir()
    size = await backup_engines.dump_engine(_Pool(), "b1", dest, "20260810-040000")

    assert size == len(payload)
    out = dest / "b1-20260810-040000.tar.gz"
    assert out.read_bytes() == payload
    assert list(dest.glob("*.partial")) == []


@pytest.mark.asyncio
async def test_dump_engine_failure_leaves_no_file(backup_root):
    """Оборванная закачка не должна выглядеть как валидный бэкап."""

    class _Client:
        def stream_meta_archive(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                raise RuntimeError("engine unavailable")
                yield  # pragma: no cover

            return cm()

    class _Pool:
        def client_for(self, _eid):
            return _Client()

    dest = backup_root / "engines"
    dest.mkdir()
    size = await backup_engines.dump_engine(_Pool(), "b6", dest, "20260810-040000")

    assert size == 0
    assert list(dest.iterdir()) == []


def test_engine_meta_archive_contains_state_dirs(tmp_path, monkeypatch):
    """Проверяем состав архива, который отдаёт движок: то, чего нет в БД."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from seeding_engine.internal_api import router

    root = tmp_path / "data"
    (root / ".fastresume").mkdir(parents=True)
    (root / ".torrents").mkdir()
    (root / ".state").mkdir()
    (root / ".fastresume" / "42.fastresume").write_bytes(b"resume")
    (root / ".torrents" / "42.torrent").write_bytes(b"meta")
    (root / ".state" / "session.state").write_bytes(b"sess")
    # Контент раздач в архив попадать не должен — он огромен.
    (root / "b1").mkdir()
    (root / "b1" / "movie.mkv").write_bytes(b"0" * 1000)

    monkeypatch.setenv("SEEDING_DATA_ROOT", str(root))
    monkeypatch.delenv("SEEDING_ENGINE_API_TOKEN", raising=False)

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/internal/v1/meta/archive")
        assert resp.status_code == 200
        assert resp.headers["X-Meta-Files"] == "3"
        blob = resp.content

    out = tmp_path / "a.tar.gz"
    out.write_bytes(blob)
    with tarfile.open(out, "r:gz") as tf:
        names = tf.getnames()
    assert "./.fastresume/42.fastresume" in names or ".fastresume/42.fastresume" in names
    assert not any("movie.mkv" in n for n in names)
