"""Роутер аутентификации (Фаза 5): вход по логину/паролю, кто я,
управление API-ключами и пользователями (admin)."""

import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from seeding_db.repository import ApiKeyRepository, SessionRepository, UserRepository

from seeding_api.auth import (
    ROLE_LEVEL,
    Principal,
    hash_key,
    hash_password,
    new_session_token,
    require_admin,
    require_auth,
    verify_password,
)
from seeding_api.deps import DbSession

router = APIRouter(tags=["auth"])

_VALID_ROLES = set(ROLE_LEVEL)


def _session_ttl() -> timedelta:
    try:
        hours = int(os.getenv("SEEDING_SESSION_TTL_HOURS", "720"))
    except ValueError:
        hours = 720
    return timedelta(hours=max(1, hours))


async def _total_admins(session, *, exclude_user_id: int | None = None,
                        exclude_key_id: int | None = None) -> int:
    """Сколько активных admin-принципалов всего (ключи + пользователи).

    Используется, чтобы не дать удалить/разжаловать последнего администратора и
    не запереть себя снаружи."""
    keys = await ApiKeyRepository(session).count_admins(exclude_id=exclude_key_id)
    users = await UserRepository(session).count_admins(exclude_id=exclude_user_id)
    return keys + users


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    role: str = "operator"


class UserUpdateIn(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)


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


@router.post("/auth/login")
async def login(body: LoginIn, session: DbSession, request: Request):
    """Вход по логину/паролю. Возвращает токен сессии (хранить как обычный ключ)."""
    # Для аудита: фиксируем, под каким именем пытались войти (даже при неуспехе).
    request.state.audit_actor = body.username.strip()
    user = await UserRepository(session).get_by_username(body.username.strip())
    # Постоянная проверка пароля даже при отсутствии пользователя — против тайминг-атак.
    stored = user.password_hash if (user and user.enabled) else (
        "pbkdf2_sha256$200000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    )
    ok = verify_password(body.password, stored)
    if not user or not user.enabled or not ok:
        raise HTTPException(status_code=401, detail="неверный логин или пароль")
    token = new_session_token()
    expires = datetime.now(timezone.utc) + _session_ttl()
    await SessionRepository(session).create(
        token_hash=hash_key(token),
        user_id=user.id,
        username=user.username,
        role=user.role,
        expires_at=expires,
    )
    await UserRepository(session).touch_login(user.id)
    await session.commit()
    return {
        "token": token,
        "username": user.username,
        "role": user.role,
        "expires_at": expires.isoformat(),
    }


@router.post("/auth/logout")
async def logout(
    session: DbSession,
    request: Request,
    _: Principal = Depends(require_auth),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    token = (x_api_key or request.query_params.get("api_key") or "").strip()
    if token:
        await SessionRepository(session).delete_by_hash(hash_key(token))
        await session.commit()
    return {"ok": True}


@router.get("/auth/me")
async def whoami(principal: Principal = Depends(require_auth)):
    return {"name": principal.name, "role": principal.role, "source": principal.source}


def _user_out(row) -> dict:
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
    }


@router.get("/auth/users")
async def list_users(session: DbSession, _: Principal = Depends(require_admin)):
    rows = await UserRepository(session).list_all()
    return [_user_out(r) for r in rows]


@router.post("/auth/users", status_code=201)
async def create_user(
    body: UserCreateIn, session: DbSession, _: Principal = Depends(require_admin)
):
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"роль должна быть одной из {sorted(_VALID_ROLES)}")
    repo = UserRepository(session)
    if await repo.get_by_username(body.username.strip()) is not None:
        raise HTTPException(status_code=409, detail="пользователь с таким именем уже есть")
    row = await repo.create(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
    )
    await session.commit()
    return _user_out(row)


@router.patch("/auth/users/{user_id}")
async def update_user(
    user_id: int, body: UserUpdateIn, session: DbSession, _: Principal = Depends(require_admin)
):
    repo = UserRepository(session)
    row = await repo.get_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="пользователь не найден")
    if body.role is not None and body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"роль должна быть одной из {sorted(_VALID_ROLES)}")
    demoting = body.role is not None and body.role != "admin" and row.role == "admin"
    disabling = body.enabled is False and row.enabled and row.role == "admin"
    if (demoting or disabling) and await _total_admins(session, exclude_user_id=user_id) == 0:
        raise HTTPException(status_code=400, detail="нельзя убрать последнего администратора")
    updated = await repo.update(
        user_id,
        role=body.role,
        enabled=body.enabled,
        password_hash=hash_password(body.password) if body.password else None,
    )
    # Смена роли/блокировка/пароль — инвалидируем активные сессии пользователя.
    if body.role is not None or body.enabled is not None or body.password:
        await SessionRepository(session).delete_for_user(user_id)
    await session.commit()
    return _user_out(updated)


@router.delete("/auth/users/{user_id}")
async def delete_user(user_id: int, session: DbSession, _: Principal = Depends(require_admin)):
    repo = UserRepository(session)
    row = await repo.get_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="пользователь не найден")
    if row.role == "admin" and row.enabled and await _total_admins(session, exclude_user_id=user_id) == 0:
        raise HTTPException(status_code=400, detail="нельзя удалить последнего администратора")
    await SessionRepository(session).delete_for_user(user_id)
    await repo.delete(user_id)
    await session.commit()
    return {"deleted": user_id}


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
