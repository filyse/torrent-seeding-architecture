import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

from fastapi import FastAPI, Request

from seeding_engine.internal_api import router as internal_router
from seeding_engine.logconf import setup_logging
from seeding_engine.torrent_runtime import build_torrent_runtime

setup_logging("engine")
log = logging.getLogger(__name__)

app = FastAPI(title="Seeding engine", version="0.1.0")
app.include_router(internal_router)


def _self_register_payload() -> dict | None:
    """Собрать данные для саморегистрации в оркестраторе (Фаза 4.5).
    Большинство значений выводится из существующих env по соглашению CT400."""
    orch = os.getenv("SEEDING_ORCHESTRATOR_URL", "").strip().rstrip("/")
    key = os.getenv("SEEDING_ENGINE_REGISTER_KEY", "").strip()
    eid = (os.getenv("SEEDING_ENGINE_ID") or os.getenv("ENGINE_STORAGE_SUBDIR") or "").strip()
    if not (orch and key and eid):
        return None
    data_root = os.getenv("SEEDING_DATA_ROOT", "/data").rstrip("/")
    # Если включён TLS — движок отдаёт https, и оркестратор должен идти по https.
    scheme = "https" if os.getenv("SEEDING_ENGINE_TLS", "0") == "1" else "http"
    url = os.getenv("SEEDING_ENGINE_ADVERTISE_URL", "").strip() or f"{scheme}://engine-{eid}:8081"
    storage_prefix = os.getenv("SEEDING_ENGINE_STORAGE_PREFIX", "").strip() or f"{data_root}/{eid}"
    media_path = os.getenv("SEEDING_ENGINE_MEDIA_PATH", "").strip() or None
    listen_port: int | None = None
    raw_lp = os.getenv("SEEDING_ENGINE_LISTEN_PORT", "").strip()
    if raw_lp.isdigit():
        listen_port = int(raw_lp)
    else:
        ifs = os.getenv("LT_LISTEN_INTERFACES", "").strip()
        if ":" in ifs:
            tail = ifs.split(",")[0].rsplit(":", 1)[-1]
            if tail.isdigit():
                listen_port = int(tail)
    return {
        "orch": orch,
        "key": key,
        "body": {
            "id": eid,
            "url": url,
            "storage_prefix": storage_prefix,
            "media_path": media_path,
            "listen_port": listen_port,
        },
    }


def _post_register(orch: str, key: str, body: dict) -> bool:
    req = urllib.request.Request(
        f"{orch}/api/v1/engines/register",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Register-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        log.debug("self-register attempt failed: %s", exc)
        return False


async def _self_register_loop() -> None:
    cfg = _self_register_payload()
    if cfg is None:
        return
    try:
        interval = max(15, int(os.getenv("SEEDING_ENGINE_HEARTBEAT_INTERVAL", "60")))
    except ValueError:
        interval = 60
    announced = False
    while True:
        ok = await asyncio.to_thread(_post_register, cfg["orch"], cfg["key"], cfg["body"])
        if ok and not announced:
            log.info("self-registered in orchestrator as %s", cfg["body"]["id"])
            announced = True
        await asyncio.sleep(interval if ok else 10)


@app.on_event("startup")
async def startup() -> None:
    setup_logging("engine")  # повторно после конфигурации логгеров uvicorn
    rt = build_torrent_runtime()
    app.state.torrent_runtime = rt
    await rt.start()
    log.info("engine runtime=%s", rt.backend_name)
    app.state.register_task = asyncio.create_task(_self_register_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "register_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
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
