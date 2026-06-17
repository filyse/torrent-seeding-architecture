"""Роутер аутентификации (Фаза 5): кто я + управление API-ключами (admin)."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from seeding_db.repository import ApiKeyRepository

from seeding_api.auth import ROLE_LEVEL, Principal, hash_key, require_admin, require_auth
from seeding_api.deps import DbSession

router = APIRouter(tags=["auth"])

_VALID_ROLES = set(ROLE_LEVEL)


class ApiKeyCreateIn(BaseModel):
    name: str = Field(default="", max_length=128)
    role: str = "operator"


class ApiKeyUpdateIn(BaseModel):
    role: str | None = None
    enabled: bool | None = None


def _key_out(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "role": row.role,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


@router.get("/auth/me")
async def whoami(principal: Principal = Depends(require_auth)):
    return {"name": principal.name, "role": principal.role, "source": principal.source}


@router.get("/auth/keys")
async def list_keys(session: DbSession, _: Principal = Depends(require_admin)):
    rows = await ApiKeyRepository(session).list_all()
    return [_key_out(r) for r in rows]


@router.post("/auth/keys", status_code=201)
async def create_key(
    body: ApiKeyCreateIn, session: DbSession, _: Principal = Depends(require_admin)
):
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"роль должна быть одной из {sorted(_VALID_ROLES)}")
    secret = "sk_" + secrets.token_urlsafe(32)
    repo = ApiKeyRepository(session)
    row = await repo.create(
        name=body.name.strip(),
        key_hash=hash_key(secret),
        prefix=secret[:10],
        role=body.role,
    )
    # Полный ключ возвращается ОДИН раз — дальше хранится только хеш.
    return {"key": secret, "item": _key_out(row)}


@router.patch("/auth/keys/{key_id}")
async def update_key(
    key_id: int, body: ApiKeyUpdateIn, session: DbSession, _: Principal = Depends(require_admin)
):
    repo = ApiKeyRepository(session)
    row = await repo.get_by_id(key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ключ не найден")
    if body.role is not None and body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"роль должна быть одной из {sorted(_VALID_ROLES)}")
    # Нельзя оставить систему без активного admin.
    demoting = body.role is not None and body.role != "admin" and row.role == "admin"
    disabling = body.enabled is False and row.enabled and row.role == "admin"
    if (demoting or disabling) and await repo.count_admins(exclude_id=key_id) == 0:
        raise HTTPException(status_code=400, detail="нельзя убрать последний admin-ключ")
    updated = await repo.update(key_id, role=body.role, enabled=body.enabled)
    return _key_out(updated)


@router.delete("/auth/keys/{key_id}")
async def delete_key(key_id: int, session: DbSession, _: Principal = Depends(require_admin)):
    repo = ApiKeyRepository(session)
    row = await repo.get_by_id(key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ключ не найден")
    if row.role == "admin" and row.enabled and await repo.count_admins(exclude_id=key_id) == 0:
        raise HTTPException(status_code=400, detail="нельзя удалить последний admin-ключ")
    await repo.delete(key_id)
    return {"deleted": key_id}
