"""Карта WAN-каналов для экрана «Сеть» в UI.

Отдаёт только СТАТИКУ: какой движок за каким аплинком и какова ёмкость канала.
Живые скорости фронт берёт из уже существующего потока агрегатов (`by_engine` в
`GET /session/stats`, SSE `/stream`, WS-канал `stats`), поэтому этот эндпоинт не
опрашивает движки и вызывается один раз при открытии экрана.
"""

from fastapi import APIRouter

from seeding_api import wan_links
from seeding_api.deps import EnginePoolDep

router = APIRouter(tags=["network"])


@router.get("/network/links")
async def network_links(pool: EnginePoolDep):
    all_links = wan_links.links()
    buckets: dict[str, list[str]] = {link.id: [] for link in all_links}
    unassigned: list[str] = []
    for spec in sorted(pool.specs, key=lambda s: s.id):
        link = wan_links.link_for_url(spec.url, all_links)
        if link is None:
            unassigned.append(spec.id)
        else:
            buckets[link.id].append(spec.id)
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
