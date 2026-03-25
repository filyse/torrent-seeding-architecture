import os
from contextlib import asynccontextmanager

from arq import create_pool
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from seeding_db.config import get_database_url
from seeding_db.session import create_engine as make_async_engine
from seeding_db.session import create_session_factory, init_models
from sqlalchemy import text

from seeding_api.arq_util import redis_settings_from_url
from seeding_api.deps import require_api_key_if_configured
from seeding_api.engine_client import EngineClient
from seeding_api.restore import maybe_restore_torrents_to_engine
from seeding_api.routers import jobs as jobs_router
from seeding_api.routers import torrents as torrents_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    url = get_database_url()
    engine = make_async_engine(url)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    if os.getenv("SEEDING_AUTO_SCHEMA", "").lower() in ("1", "true", "yes"):
        await init_models(engine)
    engine_url = os.getenv("ENGINE_URL", "http://127.0.0.1:8081")
    ec = EngineClient(engine_url)
    app.state.engine_client = ec
    await maybe_restore_torrents_to_engine(app.state.session_factory, ec)
    app.state.arq_pool = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        app.state.arq_pool = await create_pool(redis_settings_from_url(redis_url))
    yield
    if app.state.arq_pool is not None:
        await app.state.arq_pool.close()
    await ec.aclose()
    await engine.dispose()


app = FastAPI(title="Torrent seeding API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    engine_ok = False
    try:
        factory = request.app.state.session_factory
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    try:
        await request.app.state.engine_client.health()
        engine_ok = True
    except Exception:
        pass
    overall = "ok" if db_ok and engine_ok else "degraded"
    return {
        "status": overall,
        "service": "api",
        "checks": {"database": db_ok, "engine": engine_ok},
    }


@app.get("/")
async def root():
    return {"docs": "/docs", "health": "/api/v1/health"}


app.include_router(
    torrents_router.router,
    prefix="/api/v1/torrents",
    dependencies=[Depends(require_api_key_if_configured)],
)
app.include_router(
    jobs_router.router,
    prefix="/api/v1",
    dependencies=[Depends(require_api_key_if_configured)],
)
