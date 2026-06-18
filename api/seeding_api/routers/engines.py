import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from seeding_db.repository import EngineRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.schemas import (
    EngineLimitsIn,
    EngineOut,
    EngineRegisterIn,
    EngineRegistryItem,
)

router = APIRouter()
# Публичный роутер для саморегистрации движков: защищён отдельным X-Register-Key,
# поэтому НЕ должен попадать под общий require_auth (иначе движки получают 401).
public_router = APIRouter()


def _require_register_key(x_register_key: str | None) -> None:
    """Регистрация движков защищена отдельным ключом `SEEDING_ENGINE_REGISTER_KEY`.
    Если ключ не задан — функция выключена (а не открыта всем)."""
    configured = os.getenv("SEEDING_ENGINE_REGISTER_KEY", "").strip()
    if not configured:
        raise HTTPException(status_code=403, detail="engine self-registration is disabled")
    if not x_register_key or x_register_key.strip() != configured:
        raise HTTPException(status_code=401, detail="invalid or missing X-Register-Key")


@router.get("", response_model=list[EngineOut])
async def list_engines(pool: EnginePoolDep, session: DbSession):
    stats = await pool.session_stats_all()
    rows = await EngineRepository(session).list_all()
    limits = {r.id: (r.download_limit, r.upload_limit) for r in rows}
    out: list[EngineOut] = []
    for s in pool.specs:
        st = stats.get(s.id) or {}
        online = not st.get("error")
        dt = st.get("disk_total")
        df = st.get("disk_free")
        dl, ul = limits.get(s.id, (None, None))
        out.append(
            EngineOut(
                id=s.id,
                url=s.url,
                storage_prefix=s.storage_prefix,
                listen_port=s.listen_port,
                disk_total=int(dt) if dt is not None else None,
                disk_free=int(df) if df is not None else None,
                online=online,
                download_limit=dl,
                upload_limit=ul,
            )
        )
    return out


@router.post("/{engine_id}/limits", response_model=EngineOut)
async def set_engine_limits(
    engine_id: str, body: EngineLimitsIn, session: DbSession, pool: EnginePoolDep
):
    """Постоянные лимиты сессии движка (байт/с; 0 = без лимита).

    Сохраняем в реестр БД и сразу применяем к живой сессии. Значения переживают
    перезапуск движка — переприменяются при следующей саморегистрации."""
    spec = pool.spec(engine_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown engine_id: {engine_id}")
    repo = EngineRepository(session)
    row = await repo.set_limits(engine_id, body.download_limit, body.upload_limit)
    if row is None:
        # Статический движок без записи в реестре — заводим её, затем ставим лимиты.
        await repo.upsert(
            engine_id=engine_id,
            url=spec.url,
            storage_prefix=spec.storage_prefix,
            media_path=spec.media_path,
            listen_port=spec.listen_port,
        )
        row = await repo.set_limits(engine_id, body.download_limit, body.upload_limit)
    await session.commit()

    online = True
    try:
        await pool.client_for(engine_id).set_session_limits(
            row.download_limit or 0, row.upload_limit or 0
        )
    except (KeyError, httpx.HTTPError):
        online = False  # лимиты сохранены, применятся при следующей регистрации движка

    return EngineOut(
        id=row.id,
        url=row.url or spec.url,
        storage_prefix=row.storage_prefix or spec.storage_prefix,
        listen_port=row.listen_port if row.listen_port is not None else spec.listen_port,
        online=online,
        download_limit=row.download_limit,
        upload_limit=row.upload_limit,
    )


@router.get("/registry", response_model=list[EngineRegistryItem])
async def engine_registry(session: DbSession, pool: EnginePoolDep):
    """Полный реестр движков с last_seen/staleness (включая выбывшие, которых нет в активном пуле)."""
    rows = await EngineRepository(session).list_all()
    db_by_id = {r.id: r for r in rows}
    ttl = pool.ttl_seconds()
    now = datetime.now(timezone.utc)
    pool_ids = {s.id for s in pool.specs}
    static_ids = pool.static_ids
    out: list[EngineRegistryItem] = []
    for eid in sorted(set(db_by_id) | static_ids):
        r = db_by_id.get(eid)
        spec = pool.spec(eid)
        last_seen = r.last_seen if r else None
        age: int | None = None
        stale = False
        if last_seen is not None:
            ls = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
            age = int((now - ls).total_seconds())
            stale = age > ttl and eid not in static_ids
        elif eid not in static_ids:
            stale = True
        in_static, in_db = eid in static_ids, r is not None
        source = "static+dynamic" if (in_static and in_db) else ("static" if in_static else "dynamic")
        out.append(
            EngineRegistryItem(
                id=eid,
                url=(spec.url if spec else (r.url if r else "")),
                storage_prefix=(spec.storage_prefix if spec else (r.storage_prefix if r else "")),
                media_path=(spec.media_path if spec else (r.media_path if r else None)),
                listen_port=(spec.listen_port if spec else (r.listen_port if r else None)),
                enabled=(r.enabled if r else True),
                last_seen=last_seen,
                age_seconds=age,
                stale=stale,
                in_pool=eid in pool_ids,
                source=source,
            )
        )
    return out


@router.get("/{engine_id}/connectivity")
async def engine_connectivity(engine_id: str, pool: EnginePoolDep):
    """Проверка связности с движком (онбординг/диагностика):
    - reachable + api_latency_ms: достучался ли оркестратор до внутреннего API движка;
    - bt: статус BitTorrent (слушает ли порт, был ли входящий коннект = порт открыт снаружи)."""
    spec = pool.spec(engine_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown engine_id: {engine_id}")
    try:
        client = pool.client_for(engine_id)
    except KeyError:
        raise HTTPException(status_code=409, detail=f"engine {engine_id} not in active pool (stale?)")

    result: dict = {
        "id": engine_id,
        "url": spec.url,
        "tls": spec.url.startswith("https://"),
        "reachable": False,
        "api_latency_ms": None,
        "bt": None,
        "error": None,
    }
    t0 = time.perf_counter()
    try:
        await client.health()
        result["reachable"] = True
        result["api_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    except httpx.HTTPError as exc:
        result["error"] = f"api unreachable: {exc}"
        return result

    try:
        bt = await client.net_status()
        result["bt"] = bt
        port = (spec.listen_port or bt.get("configured_port"))
        result["bt_listening"] = bool(bt.get("listening"))
        # has_incoming=True — кто-то снаружи подключился к BT-порту: порт точно доступен.
        result["bt_reachable_hint"] = bt.get("has_incoming")
        result["bt_port"] = port
    except httpx.HTTPError as exc:
        result["bt"] = {"error": str(exc)}
    return result


@public_router.post("/register", response_model=EngineOut)
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
    # Переприменяем постоянные лимиты (движок мог перезапуститься и сбросить сессию).
    if row.download_limit is not None or row.upload_limit is not None:
        try:
            await pool.client_for(row.id).set_session_limits(
                row.download_limit or 0, row.upload_limit or 0
            )
        except (KeyError, httpx.HTTPError):
            pass
    return EngineOut(
        id=row.id,
        url=row.url,
        storage_prefix=row.storage_prefix,
        listen_port=row.listen_port,
        online=True,
        download_limit=row.download_limit,
        upload_limit=row.upload_limit,
    )
