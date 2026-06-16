import os

from fastapi import APIRouter, Header, HTTPException, Request
from seeding_db.repository import EngineRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.schemas import EngineOut, EngineRegisterIn

router = APIRouter()


def _require_register_key(x_register_key: str | None) -> None:
    """Регистрация движков защищена отдельным ключом `SEEDING_ENGINE_REGISTER_KEY`.
    Если ключ не задан — функция выключена (а не открыта всем)."""
    configured = os.getenv("SEEDING_ENGINE_REGISTER_KEY", "").strip()
    if not configured:
        raise HTTPException(status_code=403, detail="engine self-registration is disabled")
    if not x_register_key or x_register_key.strip() != configured:
        raise HTTPException(status_code=401, detail="invalid or missing X-Register-Key")


@router.get("", response_model=list[EngineOut])
async def list_engines(pool: EnginePoolDep):
    stats = await pool.session_stats_all()
    out: list[EngineOut] = []
    for s in pool.specs:
        st = stats.get(s.id) or {}
        online = not st.get("error")
        dt = st.get("disk_total")
        df = st.get("disk_free")
        out.append(
            EngineOut(
                id=s.id,
                url=s.url,
                storage_prefix=s.storage_prefix,
                listen_port=s.listen_port,
                disk_total=int(dt) if dt is not None else None,
                disk_free=int(df) if df is not None else None,
                online=online,
            )
        )
    return out


@router.post("/register", response_model=EngineOut)
async def register_engine(
    body: EngineRegisterIn,
    request: Request,
    session: DbSession,
    pool: EnginePoolDep,
    x_register_key: str | None = Header(None, alias="X-Register-Key"),
):
    """Саморегистрация движка по ключу (Фаза 4.5): добавляет/обновляет запись в реестре БД
    и сразу пересобирает пул, чтобы движок стал доступен без перезапуска оркестратора."""
    _require_register_key(x_register_key)
    repo = EngineRepository(session)
    row = await repo.upsert(
        engine_id=body.id.strip(),
        url=body.url.strip(),
        storage_prefix=body.storage_prefix.strip(),
        media_path=(body.media_path or "").strip() or None,
        listen_port=body.listen_port,
    )
    await session.commit()
    await pool.refresh()
    return EngineOut(
        id=row.id,
        url=row.url,
        storage_prefix=row.storage_prefix,
        listen_port=row.listen_port,
        online=True,
    )
