"""Микросервис чанковой загрузки (sidecar, откат). Публичный префикс /upload/v1."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from seeding_upload import __version__
from seeding_upload.router import build_upload_router
from seeding_upload.storage import UploadStorage


def _env_roots() -> dict[str, Path]:
    raw = os.getenv("UPLOAD_ENGINE_ROOTS", "").strip()
    if not raw:
        root = os.getenv("UPLOAD_DATA_ROOT", "/data")
        return {"dev": Path(root)}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("UPLOAD_ENGINE_ROOTS must be a JSON object")
    return {str(k): Path(str(v)) for k, v in data.items()}


def _chunk_size() -> int:
    return max(256 * 1024, int(os.getenv("UPLOAD_CHUNK_SIZE", str(8 * 1024 * 1024))))


def _gc_minutes() -> float:
    return float(os.getenv("UPLOAD_GC_MINUTES", "30"))


def _secret() -> str:
    return os.getenv("SEEDING_UPLOAD_TICKET_SECRET", "").strip()


def _cors_origins() -> list[str]:
    raw = os.getenv("UPLOAD_CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [x.strip() for x in raw.split(",") if x.strip()]


storage = UploadStorage(_env_roots(), _chunk_size(), _gc_minutes())


async def _gc_loop() -> None:
    while True:
        try:
            storage.gc_once()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_gc_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="seeding-upload", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(build_upload_router(storage, _secret))


@app.get("/health")
async def health():
    return {
        "ok": True,
        "version": __version__,
        "engines": sorted(storage.roots.keys()),
        "chunk_size": storage.chunk_size,
        "mode": "sidecar",
    }
