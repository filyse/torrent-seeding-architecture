import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from seeding_engine.internal_api import router as internal_router
from seeding_engine.torrent_runtime import build_torrent_runtime

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = build_torrent_runtime()
    app.state.torrent_runtime = rt
    await rt.start()
    log.info("engine runtime=%s", rt.backend_name)
    yield
    await rt.stop()


app = FastAPI(title="Seeding engine", version="0.1.0", lifespan=lifespan)
app.include_router(internal_router)


@app.get("/health")
async def health(request: Request):
    rt = request.app.state.torrent_runtime
    return {
        "status": "ok",
        "service": "engine",
        "backend": rt.backend_name,
        "data_root": os.getenv("SEEDING_DATA_ROOT", ""),
    }


@app.get("/")
async def root():
    return {"internal": "/internal/v1", "health": "/health"}
