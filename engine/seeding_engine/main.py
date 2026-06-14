import logging
import os

from fastapi import FastAPI, Request

from seeding_engine.internal_api import router as internal_router
from seeding_engine.torrent_runtime import build_torrent_runtime

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

app = FastAPI(title="Seeding engine", version="0.1.0")
app.include_router(internal_router)


@app.on_event("startup")
async def startup() -> None:
    rt = build_torrent_runtime()
    app.state.torrent_runtime = rt
    await rt.start()
    log.info("engine runtime=%s", rt.backend_name)


@app.on_event("shutdown")
async def shutdown() -> None:
    rt = getattr(app.state, "torrent_runtime", None)
    if rt is not None:
        await rt.stop()


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
