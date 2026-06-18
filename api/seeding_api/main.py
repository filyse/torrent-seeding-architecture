import asyncio
import os

from arq import create_pool
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from seeding_db.config import get_database_url
from seeding_db.session import create_engine as make_async_engine
from seeding_db.session import create_session_factory, init_models
from sqlalchemy import text

from seeding_api import maintenance
from seeding_api.arq_util import redis_settings_from_url
from seeding_api.auth import require_auth
from seeding_api.engine_pool import EnginePool
from seeding_api.restore import maybe_restore_torrents_to_engine
from seeding_api.routers import auth as auth_router
from seeding_api.routers import backups as backups_router
from seeding_api.routers import engines as engines_router
from seeding_api.routers import health as health_router
from seeding_api.routers import jobs as jobs_router
from seeding_api.routers import session as session_router
from seeding_api.routers import stream as stream_router
from seeding_api.routers import torrents as torrents_router

app = FastAPI(title="Torrent seeding API", version="0.2.0")


def _engine_refresh_interval() -> int:
    try:
        return max(0, int(os.getenv("SEEDING_ENGINE_REFRESH_INTERVAL", "30")))
    except ValueError:
        return 30


async def _engine_refresh_loop(pool: EnginePool) -> None:
    """Периодически подхватывать только что зарегистрированные движки из БД."""
    interval = _engine_refresh_interval()
    if interval <= 0:
        return
    try:
        while True:
            await asyncio.sleep(interval)
            await pool.refresh()
    except asyncio.CancelledError:
        raise


@app.on_event("startup")
async def startup() -> None:
    url = get_database_url()
    engine = make_async_engine(url)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    if os.getenv("SEEDING_AUTO_SCHEMA", "").lower() in ("1", "true", "yes"):
        await init_models(engine)
    pool = EnginePool(session_factory=app.state.session_factory)
    await pool.refresh()
    app.state.engine_pool = pool
    await maybe_restore_torrents_to_engine(app.state.session_factory, pool)
    app.state.arq_pool = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        app.state.arq_pool = await create_pool(redis_settings_from_url(redis_url))
    app.state.engine_refresh_task = asyncio.create_task(_engine_refresh_loop(pool))


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "engine_refresh_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    if getattr(app.state, "arq_pool", None) is not None:
        await app.state.arq_pool.close()
    if getattr(app.state, "engine_pool", None) is not None:
        await app.state.engine_pool.aclose()
    if getattr(app.state, "db_engine", None) is not None:
        await app.state.db_engine.dispose()

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Маршруты, доступные даже в режиме обслуживания (здоровье, вход, бэкапы).
_MAINTENANCE_ALLOW = ("/api/v1/health", "/api/v1/auth", "/api/v1/backups")


@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    on, reason = maintenance.status()
    if on:
        path = request.url.path
        if path.startswith("/api/v1") and not path.startswith(_MAINTENANCE_ALLOW):
            return JSONResponse(
                status_code=503,
                content={"error": {"code": 503, "message": reason or "Идёт обслуживание"}},
            )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.status_code, "message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": 422, "message": "validation_failed", "fields": exc.errors()}},
    )


@app.get("/api/v1/health")
async def health(request: Request):
    db_ok = False
    try:
        factory = request.app.state.session_factory
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    pool: EnginePool = request.app.state.engine_pool
    engines = await pool.health_all()
    engines_ok = all(engines.values()) if engines else False
    overall = "ok" if db_ok and engines_ok else "degraded"
    return {
        "status": overall,
        "service": "api",
        "checks": {
            "database": db_ok,
            "engines": engines,
            "engine": engines_ok,
        },
    }


@app.get("/")
async def root():
    return {"docs": "/docs", "health": "/api/v1/health"}


app.include_router(
    auth_router.router,
    prefix="/api/v1",
)
app.include_router(
    backups_router.router,
    prefix="/api/v1",
)
app.include_router(
    session_router.router,
    prefix="/api/v1",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    torrents_router.router,
    prefix="/api/v1/torrents",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    engines_router.public_router,
    prefix="/api/v1/engines",
)
app.include_router(
    engines_router.router,
    prefix="/api/v1/engines",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    jobs_router.router,
    prefix="/api/v1",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    health_router.router,
    prefix="/api/v1",
    dependencies=[Depends(require_auth)],
)
# SSE: ключ проверяется внутри (query-параметр), т.к. EventSource не шлёт заголовки.
app.include_router(stream_router.router, prefix="/api/v1")
