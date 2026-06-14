from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _pool(request: Request):
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="queue unavailable (set REDIS_URL)")
    return pool


@router.post("/noop")
async def enqueue_noop(request: Request):
    """Ставит в очередь задачу `noop_report` (см. `queue/seeding_queue/worker.py`)."""
    await _pool(request).enqueue_job("noop_report")
    return {"enqueued": True, "job": "noop_report"}


@router.post("/engine-health-check")
async def enqueue_engine_health_check(request: Request):
    """Фоновая проверка health всех движков."""
    await _pool(request).enqueue_job("check_engine_health")
    return {"enqueued": True, "job": "check_engine_health"}


@router.post("/sync-runtime")
async def enqueue_sync_runtime(request: Request):
    """Сверка runtime всех движков с БД."""
    await _pool(request).enqueue_job("sync_runtime_to_db", _job_id="sync-runtime-to-db")
    return {"enqueued": True, "job": "sync_runtime_to_db"}


@router.post("/bulk-register/{engine_id}")
async def enqueue_bulk_register(engine_id: str, request: Request):
    """Bulk-регистрация queued торрентов одного движка через очередь."""
    await _pool(request).enqueue_job("bulk_register_engine", engine_id)
    return {"enqueued": True, "job": "bulk_register_engine", "engine_id": engine_id}


@router.post("/restore-engine/{engine_id}")
async def enqueue_restore_engine(engine_id: str, request: Request):
    """Параллельное восстановление торрентов одного движка через очередь."""
    await _pool(request).enqueue_job("restore_engine", engine_id)
    return {"enqueued": True, "job": "restore_engine", "engine_id": engine_id}


@router.post("/restore-all")
async def enqueue_restore_all(request: Request):
    """Восстановление всех движков из реестра."""
    await _pool(request).enqueue_job("restore_all_engines")
    return {"enqueued": True, "job": "restore_all_engines"}
