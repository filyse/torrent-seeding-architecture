"""Карта WAN-каналов для экрана «Сеть» в UI.

Отдаёт только СТАТИКУ: какой движок за каким аплинком и какова ёмкость канала.
Живые скорости фронт берёт из уже существующего потока агрегатов (`by_engine` в
`GET /session/stats`, SSE `/stream`, WS-канал `stats`), поэтому этот эндпоинт не
опрашивает движки и вызывается один раз при открытии экрана.

`GET /network/uploaded-history` — дельты отдачи за корзину (час/день) из
`upload_samples`. Фронт дельты не считает.

`POST /network/links/{id}/limits` — штамп тех же постоянных лимитов на все движки
канала (не потолок суммы).
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from seeding_db.repository import UploadSampleRepository

from seeding_api import wan_links
from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.routers.engines import persist_and_apply_engine_limits
from seeding_api.schemas import (
    EngineLimitsIn,
    UploadedHistoryBucket,
    UploadedHistoryOut,
    UploadedHistoryTotals,
    WanLimitsOut,
)

router = APIRouter(tags=["network"])


def _engine_pairs(pool) -> list[tuple[str, str]]:
    return [(spec.id, spec.url) for spec in sorted(pool.specs, key=lambda s: s.id)]


@router.get("/network/uploaded-history", response_model=UploadedHistoryOut)
async def uploaded_history(
    session: DbSession,
    pool: EnginePoolDep,
    period: Literal["day", "week", "month"] = Query("week"),
    metric: Literal["uploaded", "downloaded"] = Query("uploaded"),
):
    """Объём отдачи или приёма за корзину: день (24 часа), неделя (7 дней), месяц (30 дней)."""
    all_links = wan_links.links()
    wan_ids = [link.id for link in all_links]
    engine_wan = wan_links.engine_wan_map((spec.id, spec.url) for spec in pool.specs)
    hist = await UploadSampleRepository(session).history(
        period=period,
        now=datetime.now(timezone.utc),
        wan_ids=wan_ids,
        engine_wan=engine_wan,
        metric=metric,
    )
    return UploadedHistoryOut(
        period=period,
        buckets=[
            UploadedHistoryBucket(
                t=b.t, farm=b.farm, wan=b.wan, engines=b.engines, sampled=b.sampled
            )
            for b in hist.buckets
        ],
        total=UploadedHistoryTotals(farm=hist.total.farm, wan=hist.total.wan),
        previous_total=UploadedHistoryTotals(
            farm=hist.previous_total.farm, wan=hist.previous_total.wan
        ),
        first_sampled_at=hist.first_sampled_at,
        last_sampled_at=hist.last_sampled_at,
    )


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
