"""SSE-поток агрегированной статистики сессии (для живой панели сверху).

Раньше поток слал и весь список раздач — на тысячах торрентов это означало N+1 к движкам
каждые N секунд и мегабайты трафика. Список теперь грузится постранично (см. GET /torrents),
а поток отдаёт только лёгкие агрегаты (несколько запросов session/stats на движок).

EventSource не умеет слать заголовки, поэтому API-ключ (если настроен) принимается
query-параметром `api_key`.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from seeding_api.auth import resolve_principal

log = logging.getLogger(__name__)

router = APIRouter()


async def _build_snapshot(session_factory, pool) -> dict:
    by_engine = await pool.session_stats_all()
    stats = pool.aggregate_session_stats(by_engine)
    return {"stats": stats}


@router.get("/stream")
async def stream(
    request: Request,
    api_key: str | None = Query(None),
    interval: float = Query(3.0),
):
    # SSE — чтение, достаточно роли viewer; ключ приходит query-параметром.
    principal = await resolve_principal(request, api_key)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    interval = min(max(interval, 1.0), 30.0)
    session_factory = request.app.state.session_factory
    pool = request.app.state.engine_pool

    async def gen():
        # Подсказка nginx/прокси: не буферизировать поток.
        yield ": stream open\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                snap = await _build_snapshot(session_factory, pool)
                payload = json.dumps(snap, ensure_ascii=False)
                yield f"event: snapshot\ndata: {payload}\n\n"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("sse snapshot failed: %s", exc)
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
