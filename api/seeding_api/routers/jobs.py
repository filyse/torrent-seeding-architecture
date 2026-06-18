from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException, Request

from seeding_api.auth import Principal, require_admin

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _pool(request: Request):
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="queue unavailable (set REDIS_URL)")
    return pool


async def _enqueue(request: Request, fn: str, *args, job_id: str | None = None) -> dict:
    """Поставить задачу и вернуть её job_id, чтобы UI мог опросить результат.

    enqueue_job возвращает None, если задача с таким job_id уже в очереди/выполняется —
    тогда отдаём известный job_id (для задач с фиксированным id) либо помечаем дубль."""
    job = await _pool(request).enqueue_job(fn, *args, _job_id=job_id)
    jid = job.job_id if job is not None else job_id
    return {"enqueued": job is not None, "job": fn, "job_id": jid}


@router.post("/noop")
async def enqueue_noop(request: Request, _: Principal = Depends(require_admin)):
    """Ставит в очередь задачу `noop_report` (см. `queue/seeding_queue/worker.py`)."""
    return await _enqueue(request, "noop_report")


@router.post("/engine-health-check")
async def enqueue_engine_health_check(request: Request, _: Principal = Depends(require_admin)):
    """Фоновая проверка health всех движков."""
    return await _enqueue(request, "check_engine_health")


@router.post("/sync-runtime")
async def enqueue_sync_runtime(request: Request, _: Principal = Depends(require_admin)):
    """Сверка runtime всех движков с БД."""
    return await _enqueue(request, "sync_runtime_to_db", job_id="sync-runtime-to-db")


@router.post("/bulk-register/{engine_id}")
async def enqueue_bulk_register(
    engine_id: str, request: Request, _: Principal = Depends(require_admin)
):
    """Bulk-регистрация queued торрентов одного движка через очередь."""
    out = await _enqueue(request, "bulk_register_engine", engine_id)
    out["engine_id"] = engine_id
    return out


@router.post("/restore-engine/{engine_id}")
async def enqueue_restore_engine(
    engine_id: str, request: Request, _: Principal = Depends(require_admin)
):
    """Параллельное восстановление торрентов одного движка через очередь."""
    out = await _enqueue(request, "restore_engine", engine_id)
    out["engine_id"] = engine_id
    return out


@router.post("/restore-all")
async def enqueue_restore_all(request: Request, _: Principal = Depends(require_admin)):
    """Восстановление всех движков из реестра."""
    return await _enqueue(request, "restore_all_engines")


@router.get("/result/{job_id}")
async def job_result(job_id: str, request: Request, _: Principal = Depends(require_admin)):
    """Статус и результат задачи по job_id (для отображения исхода в UI)."""
    job = Job(job_id, redis=_pool(request))
    status = await job.status()
    out: dict = {"job_id": job_id, "status": getattr(status, "value", str(status))}
    if status == JobStatus.complete:
        info = await job.result_info()
        if info is not None:
            out["success"] = bool(info.success)
            out["result"] = info.result if info.success else str(info.result)
    return out
