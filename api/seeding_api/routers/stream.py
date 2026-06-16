"""SSE-поток состояния: периодические снапшоты списка раздач + агрегированной статистики.

Заменяет клиентский поллинг одним long-lived соединением. EventSource не умеет слать
заголовки, поэтому API-ключ (если настроен) принимается query-параметром `api_key`.
"""

import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from seeding_db.repository import TorrentRepository

from seeding_api.runtime_sync import merge_runtime_into_row
from seeding_api.schemas import TorrentOut

log = logging.getLogger(__name__)

router = APIRouter()


def _check_api_key(api_key: str | None) -> None:
    raw = os.getenv("SEEDING_API_KEYS", "").strip()
    if not raw:
        return
    allowed = {k.strip() for k in raw.split(",") if k.strip()}
    if not api_key or api_key not in allowed:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


async def _build_snapshot(session_factory, pool) -> dict:
    async with session_factory() as session:
        repo = TorrentRepository(session)
        rows = await repo.list_all()
        torrents: list[dict] = []
        for row in rows:
            runtime = None
            try:
                runtime = await pool.client_for_row(row).runtime_snapshot(row.id)
            except httpx.HTTPError:
                runtime = None
            status = await merge_runtime_into_row(repo, row, runtime)
            data = TorrentOut.model_validate(row).model_dump(mode="json")
            data["status"] = status
            data["runtime"] = runtime
            torrents.append(data)
        await session.commit()
    by_engine = await pool.session_stats_all()
    stats = pool.aggregate_session_stats(by_engine)
    return {"torrents": torrents, "stats": stats}


@router.get("/stream")
async def stream(
    request: Request,
    api_key: str | None = Query(None),
    interval: float = Query(3.0),
):
    _check_api_key(api_key)
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
