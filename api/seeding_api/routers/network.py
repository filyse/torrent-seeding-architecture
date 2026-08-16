"""Карта WAN-каналов для экрана «Сеть» в UI.

Отдаёт только СТАТИКУ: какой движок за каким аплинком и какова ёмкость канала.
Живые скорости фронт берёт из уже существующего потока агрегатов (`by_engine` в
`GET /session/stats`, SSE `/stream`, WS-канал `stats`), поэтому этот эндпоинт не
опрашивает движки и вызывается один раз при открытии экрана.

`POST /network/links/{id}/limits` — штамп тех же постоянных лимитов на все движки
канала (не потолок суммы).
"""

from fastapi import APIRouter, HTTPException

from seeding_api import wan_links
from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.routers.engines import persist_and_apply_engine_limits
from seeding_api.schemas import EngineLimitsIn, WanLimitsOut

router = APIRouter(tags=["network"])


def _engine_pairs(pool) -> list[tuple[str, str]]:
    return [(spec.id, spec.url) for spec in sorted(pool.specs, key=lambda s: s.id)]


@router.get("/network/links")
async def network_links(pool: EnginePoolDep):
    all_links = wan_links.links()
    buckets, unassigned = wan_links.assign_engines(_engine_pairs(pool), all_links)
    return {
        "links": [
            {
                "id": link.id,
                "name": link.name,
                "router": link.router,
                "wan_ip": link.wan_ip,
                "capacity_bps": link.capacity_bps,
                "engines": buckets[link.id],
            }
            for link in all_links
        ],
        "unassigned": unassigned,
    }


@router.post("/network/links/{wan_id}/limits", response_model=WanLimitsOut)
async def set_wan_limits(
    wan_id: str, body: EngineLimitsIn, session: DbSession, pool: EnginePoolDep
):
    """Одно число на каждый движок канала. Это не потолок суммы аплинка.

    Пишется в реестр как обычный лимит движка и сразу применяется к живой сессии.
    """
    all_links = wan_links.links()
    link = wan_links.link_by_id(wan_id, all_links)
    if link is None:
        raise HTTPException(status_code=404, detail=f"unknown wan_id: {wan_id}")
    buckets, _ = wan_links.assign_engines(_engine_pairs(pool), all_links)
    applied = 0
    saved_ids: list[str] = []
    for engine_id in buckets[link.id]:
        row = await persist_and_apply_engine_limits(engine_id, body, session, pool)
        saved_ids.append(row.id)
        if row.online:
            applied += 1
    return WanLimitsOut(
        id=link.id,
        name=link.name,
        engines=saved_ids,
        applied=applied,
        saved=len(saved_ids),
        download_limit=body.download_limit if (body.download_limit and body.download_limit > 0) else None,
        upload_limit=body.upload_limit if (body.upload_limit and body.upload_limit > 0) else None,
    )
