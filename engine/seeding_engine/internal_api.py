from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class TorrentRegisterIn(BaseModel):
    db_id: int = Field(..., ge=1)
    magnet_uri: str | None = None
    save_path: str = Field(..., min_length=1)


class RuntimeHandleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    db_id: int
    magnet_uri: str | None
    save_path: str
    runtime_status: str
    info_hash: str | None = None
    progress: float | None = None
    lt_state: str | None = None


def get_runtime(request: Request):
    return request.app.state.torrent_runtime


@router.get("/health")
async def internal_health(request: Request):
    rt = get_runtime(request)
    return {"status": "ok", "service": "engine", "backend": rt.backend_name}


@router.get("/torrents", response_model=list[RuntimeHandleOut])
async def list_runtime_torrents(request: Request):
    rt = get_runtime(request)
    rows = await rt.list_all()
    return [RuntimeHandleOut.model_validate(r) for r in rows]


@router.post("/torrents", response_model=RuntimeHandleOut)
async def register_torrent(request: Request, body: TorrentRegisterIn):
    rt = get_runtime(request)
    try:
        h = await rt.add_torrent(body.db_id, body.magnet_uri, body.save_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeHandleOut.model_validate(h)


@router.get("/torrents/{db_id}", response_model=RuntimeHandleOut)
async def get_runtime_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    h = await rt.get(db_id)
    if h is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return RuntimeHandleOut.model_validate(h)


@router.post("/torrents/{db_id}/pause", response_model=RuntimeHandleOut)
async def pause_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    h = await rt.pause(db_id)
    if h is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return RuntimeHandleOut.model_validate(h)


@router.post("/torrents/{db_id}/resume", response_model=RuntimeHandleOut)
async def resume_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    h = await rt.resume(db_id)
    if h is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return RuntimeHandleOut.model_validate(h)


@router.delete("/torrents/{db_id}", status_code=204)
async def remove_runtime_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    ok = await rt.remove(db_id)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
