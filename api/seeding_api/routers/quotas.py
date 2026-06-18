"""Квоты по объёму отдачи на метку (Фаза 5).

Чтение — любая роль; изменение — operator+. Счётчик копит воркер (`sync_runtime_to_db`),
здесь — настройка квоты, сброс счётчика и возобновление приостановленных раздач.
"""

import json

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from seeding_db.models import TorrentStatus
from seeding_db.repository import QuotaRepository, TorrentRepository

from seeding_api.deps import DbSession, EnginePoolDep

router = APIRouter(tags=["quotas"])


class QuotaIn(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    upload_quota: int | None = Field(default=None, ge=0)
    enabled: bool = True


def _out(q) -> dict:
    quota = int(q.upload_quota) if q.upload_quota else 0
    used = int(q.uploaded_total or 0)
    percent = round(used / quota * 100, 1) if quota > 0 else None
    paused = []
    try:
        paused = json.loads(q.paused_ids or "[]")
    except (ValueError, TypeError):
        paused = []
    return {
        "label": q.label,
        "upload_quota": q.upload_quota,
        "uploaded_total": used,
        "enabled": q.enabled,
        "exceeded": q.exceeded,
        "percent": percent,
        "paused_count": len(paused),
        "since": q.since.isoformat() if q.since else None,
    }


@router.get("/quotas")
async def list_quotas(session: DbSession):
    rows = await QuotaRepository(session).list_quotas()
    return [_out(q) for q in rows]


@router.post("/quotas")
async def upsert_quota(body: QuotaIn, session: DbSession):
    repo = QuotaRepository(session)
    q = await repo.upsert_quota(
        body.label.strip(), upload_quota=body.upload_quota, enabled=body.enabled
    )
    await session.commit()
    return _out(q)


@router.post("/quotas/{label}/reset")
async def reset_quota(label: str, session: DbSession, pool: EnginePoolDep):
    repo = QuotaRepository(session)
    q = await repo.get_quota(label)
    if q is None:
        raise HTTPException(status_code=404, detail="квота не найдена")
    try:
        paused_ids = json.loads(q.paused_ids or "[]")
    except (ValueError, TypeError):
        paused_ids = []
    trepo = TorrentRepository(session)
    resumed = 0
    for tid in paused_ids:
        row = await trepo.get_by_id(int(tid))
        if row is None:
            continue
        try:
            await pool.client_for(row.engine_id).resume(int(tid))
            await trepo.update_status(int(tid), TorrentStatus.seeding.value)
            resumed += 1
        except (KeyError, httpx.HTTPError):
            pass
    await repo.reset_quota(label)
    await session.commit()
    return {"label": label, "resumed": resumed}


@router.delete("/quotas/{label}")
async def delete_quota(label: str, session: DbSession):
    ok = await QuotaRepository(session).delete_quota(label)
    if not ok:
        raise HTTPException(status_code=404, detail="квота не найдена")
    await session.commit()
    return {"deleted": label}
