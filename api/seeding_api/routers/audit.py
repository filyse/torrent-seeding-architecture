"""Роутер аудит-лога (Фаза 5): просмотр журнала действий (только admin)."""

from fastapi import APIRouter, Depends, Query
from seeding_db.repository import AuditRepository

from seeding_api.auth import Principal, require_admin
from seeding_api.deps import DbSession

router = APIRouter(tags=["audit"])


def _out(row) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor": row.actor,
        "role": row.role,
        "method": row.method,
        "path": row.path,
        "status": row.status,
        "ip": row.ip,
        "summary": row.summary,
    }


@router.get("/audit")
async def list_audit(
    session: DbSession,
    _: Principal = Depends(require_admin),
    limit: int = Query(200, ge=1, le=1000),
    actor: str | None = None,
):
    rows = await AuditRepository(session).list_recent(limit=limit, actor=actor)
    return [_out(r) for r in rows]
