from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/noop")
async def enqueue_noop(request: Request):
    """Ставит в очередь задачу `noop_report` (см. `queue/seeding_queue/worker.py`)."""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="queue unavailable (set REDIS_URL)")
    await pool.enqueue_job("noop_report")
    return {"enqueued": True, "job": "noop_report"}


@router.post("/engine-health-check")
async def enqueue_engine_health_check(request: Request):
    """Фоновая проверка `GET {ENGINE_URL}/health` (см. `check_engine_health` в воркере)."""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="queue unavailable (set REDIS_URL)")
    await pool.enqueue_job("check_engine_health")
    return {"enqueued": True, "job": "check_engine_health"}
