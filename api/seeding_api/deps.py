import os
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from seeding_api.engine_pool import EnginePool


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine_pool(request: Request) -> EnginePool:
    return request.app.state.engine_pool


async def require_api_key_if_configured(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Если задано SEEDING_API_KEYS (через запятую), требуется заголовок X-API-Key."""
    raw = os.getenv("SEEDING_API_KEYS", "").strip()
    if not raw:
        return
    allowed = {k.strip() for k in raw.split(",") if k.strip()}
    if not x_api_key or x_api_key not in allowed:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
EnginePoolDep = Annotated[EnginePool, Depends(get_engine_pool)]
