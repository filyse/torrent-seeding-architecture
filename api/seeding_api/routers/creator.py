"""Создание торрентов из контента на дисках движков.

Оркестратор проксирует к внутреннему API движка (создание/статус/отмена/байты) и
переиспользует существующий upload-pipeline для постановки созданного торрента на
раздачу (режим «создать и раздавать»).
"""

import os
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.engine_client import EngineClient
from seeding_api.schemas import (
    CreatorBrowseItem,
    CreatorSeedIn,
    CreatorTaskCreate,
    CreatorTaskOut,
    TorrentOut,
)

router = APIRouter()


def _client(pool: EnginePoolDep, engine_id: str) -> EngineClient:
    try:
        return pool.client_for(engine_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown engine_id: {engine_id}") from exc


def _task_out(engine_id: str, data: dict) -> CreatorTaskOut:
    return CreatorTaskOut.model_validate({**data, "engine_id": engine_id})


@router.get("/browse", response_model=list[CreatorBrowseItem])
async def creator_browse(
    pool: EnginePoolDep,
    engine_id: str = Query(..., min_length=1),
    path: str = Query(""),
):
    client = _client(pool, engine_id)
    try:
        items = await client.browse(path)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="browse failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return [CreatorBrowseItem.model_validate(i) for i in items]


@router.post("/tasks", response_model=CreatorTaskOut)
async def create_task(body: CreatorTaskCreate, pool: EnginePoolDep):
    client = _client(pool, body.engine_id)
    try:
        data = await client.create_torrent_task(body.source_path, body.skip_episode_check)
    except httpx.HTTPStatusError as exc:
        detail = "create failed"
        try:
            payload = exc.response.json()
            detail = payload.get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return _task_out(body.engine_id, data)


@router.get("/tasks/{engine_id}/{task_id}", response_model=CreatorTaskOut)
async def get_task(engine_id: str, task_id: int, pool: EnginePoolDep):
    client = _client(pool, engine_id)
    try:
        data = await client.get_create_task(task_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if data is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_out(engine_id, data)


@router.post("/tasks/{engine_id}/{task_id}/cancel")
async def cancel_task(engine_id: str, task_id: int, pool: EnginePoolDep):
    client = _client(pool, engine_id)
    try:
        ok = await client.cancel_create_task(task_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="task not found or not cancellable")
    return {"ok": True}


@router.get("/tasks/{engine_id}/{task_id}/download")
async def download_task(engine_id: str, task_id: int, pool: EnginePoolDep):
    client = _client(pool, engine_id)
    try:
        task = await client.get_create_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        payload = await client.get_created_torrent_bytes(task_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not payload:
        raise HTTPException(status_code=409, detail="torrent not ready")
    name = (task.get("name") or "download").strip() or "download"
    filename = f"{name}.torrent"
    try:
        filename.encode("ascii")
        disp = f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        disp = f"attachment; filename=\"download.torrent\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=payload,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": disp},
    )


@router.post("/tasks/{engine_id}/{task_id}/seed", response_model=TorrentOut, status_code=201)
async def seed_task(
    engine_id: str,
    task_id: int,
    body: CreatorSeedIn,
    session: DbSession,
    pool: EnginePoolDep,
):
    client = _client(pool, engine_id)
    try:
        task = await client.get_create_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.get("status") != "completed":
            raise HTTPException(status_code=409, detail="task is not completed")
        payload = await client.get_created_torrent_bytes(task_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not payload:
        raise HTTPException(status_code=409, detail="torrent not ready")

    save_path = str(task.get("save_path") or "").strip()
    if not save_path:
        raise HTTPException(status_code=409, detail="task has no save_path")
    display_name = body.display_name.strip() or str(task.get("name") or "").strip()

    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=display_name or os.path.basename(save_path),
        save_path=save_path,
        magnet_uri=None,
        engine_id=engine_id,
        label=body.label.strip(),
    )
    await session.flush()
    await session.refresh(row)
    try:
        await client.register_torrent_file(row.id, payload, row.save_path, seed_mode=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.update_status(row.id, TorrentStatus.downloading.value)
    await session.refresh(row)
    return row
