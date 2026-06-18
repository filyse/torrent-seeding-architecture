"""Аутентификация и роли (Фаза 5).

Модель — именованные API-ключи с ролями (без паролей/сессий). Ключ передаётся в
заголовке `X-API-Key` (или `?api_key=` для SSE). Роли по возрастанию прав:
`viewer` (только чтение) < `operator` (управление раздачами) < `admin` (всё + ключи).

Доступ проверяется по HTTP-методу: безопасные методы (GET/HEAD/OPTIONS) требуют viewer,
изменяющие (POST/PUT/PATCH/DELETE) — operator. Управление ключами — отдельно admin.

Совместимость: ключи из env `SEEDING_API_KEYS` считаются admin (bootstrap-доступ).
Если ключей нет вообще (ни env, ни в БД) — доступ открыт как admin, чтобы можно было
завести первый ключ; как только появляется хотя бы один ключ — включается проверка.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request
from seeding_db.repository import ApiKeyRepository, SessionRepository

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SESSION_PREFIX = "ses_"

# Троттлинг записи last_used_at: не чаще раза в N секунд на ключ/сессию.
_LAST_TOUCH: dict[int, float] = {}
_LAST_TOUCH_SESS: dict[str, float] = {}
_TOUCH_INTERVAL = 60.0


@dataclass
class Principal:
    name: str
    role: str
    source: str  # "env" | "db" | "session" | "anonymous"
    key_id: int | None = None

    @property
    def level(self) -> int:
        return ROLE_LEVEL.get(self.role, 0)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def new_session_token() -> str:
    return SESSION_PREFIX + secrets.token_urlsafe(32)


def _env_admin_keys() -> set[str]:
    raw = os.getenv("SEEDING_API_KEYS", "").strip()
    return {k.strip() for k in raw.split(",") if k.strip()}


async def resolve_principal(request: Request, api_key: str | None) -> Principal | None:
    """Вернуть Principal или None (нет валидных учётных данных)."""
    env_keys = _env_admin_keys()
    factory = getattr(request.app.state, "session_factory", None)

    if api_key:
        if api_key in env_keys:
            return Principal(name="env", role="admin", source="env")
        # Сессия по логину/паролю (токен ses_…).
        if api_key.startswith(SESSION_PREFIX) and factory is not None:
            token_hash = hash_key(api_key)
            async with factory() as session:
                srepo = SessionRepository(session)
                srow = await srepo.get_by_hash(token_hash)
                if srow is None:
                    return None
                exp = srow.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= datetime.now(timezone.utc):
                    await srepo.delete_by_hash(token_hash)
                    await session.commit()
                    return None
                now = time.monotonic()
                if now - _LAST_TOUCH_SESS.get(token_hash, 0.0) > _TOUCH_INTERVAL:
                    _LAST_TOUCH_SESS[token_hash] = now
                    await srepo.touch(srow.id)
                    await session.commit()
                return Principal(name=srow.username, role=srow.role, source="session")
        if factory is not None:
            key_hash = hash_key(api_key)
            async with factory() as session:
                repo = ApiKeyRepository(session)
                row = await repo.get_by_hash(key_hash)
                if row is not None and row.enabled:
                    now = time.monotonic()
                    if now - _LAST_TOUCH.get(row.id, 0.0) > _TOUCH_INTERVAL:
                        _LAST_TOUCH[row.id] = now
                        await repo.touch(row.id)
                        await session.commit()
                    return Principal(name=row.name, role=row.role, source="db", key_id=row.id)
        return None

    # Ключ не предъявлен: открыто только если учётных данных нет вообще (bootstrap).
    if env_keys:
        return None
    if factory is not None:
        async with factory() as session:
            if await ApiKeyRepository(session).count_enabled() > 0:
                return None
            from seeding_db.repository import UserRepository  # локально, чтобы избежать цикла

            users = await UserRepository(session).list_all()
            if users:
                return None
    return Principal(name="anonymous", role="admin", source="anonymous")


def _extract_key(request: Request, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    qk = request.query_params.get("api_key")
    return qk.strip() if qk else None


async def require_auth(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    principal = await resolve_principal(request, _extract_key(request, x_api_key))
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    needed = "viewer" if request.method in SAFE_METHODS else "operator"
    if principal.level < ROLE_LEVEL[needed]:
        raise HTTPException(status_code=403, detail=f"требуется роль {needed} или выше")
    request.state.principal = principal
    return principal


async def require_admin(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> Principal:
    principal = await resolve_principal(request, _extract_key(request, x_api_key))
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    if principal.level < ROLE_LEVEL["admin"]:
        raise HTTPException(status_code=403, detail="требуется роль admin")
    request.state.principal = principal
    return principal
