"""Вшитая загрузка файлов: /upload/v1 на том же процессе, что и /internal/v1."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from seeding_upload.router import build_upload_router
from seeding_upload.storage import UploadStorage

log = logging.getLogger(__name__)


def _engine_id() -> str:
    return (os.getenv("SEEDING_ENGINE_ID") or os.getenv("ENGINE_STORAGE_SUBDIR") or "").strip()


def _upload_root(eid: str) -> Path:
    data_root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
    prefix = os.getenv("SEEDING_ENGINE_STORAGE_PREFIX", "").strip()
    if prefix:
        return Path(prefix)
    nested = data_root / eid if eid else data_root
    if eid and nested.is_dir():
        return nested
    return data_root


def _chunk_size() -> int:
    return max(256 * 1024, int(os.getenv("UPLOAD_CHUNK_SIZE", str(8 * 1024 * 1024))))


def _gc_minutes() -> float:
    return float(os.getenv("UPLOAD_GC_MINUTES", "30"))


def _secret() -> str:
    return os.getenv("SEEDING_UPLOAD_TICKET_SECRET", "").strip()


def _cors_origins() -> list[str]:
    raw = os.getenv("UPLOAD_CORS_ORIGINS", "https://seedbox2.hw-s.ru,https://seedbox.hw-s.ru").strip()
    if raw == "*":
        return ["*"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def upload_enabled() -> bool:
    return bool(_secret() and _engine_id())


def mount_upload(app: FastAPI) -> UploadStorage | None:
    """Подключить /upload/v1, если задан ticket secret. Иначе загрузка на этом движке выкл."""
    eid = _engine_id()
    if not _secret() or not eid:
        log.info("upload HTTP off (need SEEDING_UPLOAD_TICKET_SECRET and SEEDING_ENGINE_ID)")
        return None
    root = _upload_root(eid)
    storage = UploadStorage({eid: root}, _chunk_size(), _gc_minutes())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_upload_router(storage, _secret, expected_engine_id=eid))
    log.info("upload HTTP on engine=%s root=%s", eid, root)
    return storage


async def upload_gc_loop(storage: UploadStorage) -> None:
    while True:
        try:
            storage.gc_once()
        except Exception:  # noqa: BLE001
            log.exception("upload gc failed")
        await asyncio.sleep(60)
