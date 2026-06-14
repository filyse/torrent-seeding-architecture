import base64

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/internal/v1", tags=["internal"])


class TorrentRegisterIn(BaseModel):
    db_id: int = Field(..., ge=1)
    magnet_uri: str | None = None
    torrent_b64: str | None = None
    save_path: str = Field(..., min_length=1)


class TorrentPeerOut(BaseModel):
    endpoint: str
    client: str | None = None
    progress: float | None = None
    download_rate: int | None = None
    upload_rate: int | None = None
    flags: str | None = None
    source: str | None = None


class RuntimeHandleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    db_id: int
    magnet_uri: str | None
    save_path: str
    runtime_status: str
    info_hash: str | None = None
    progress: float | None = None
    lt_state: str | None = None
    download_rate: int | None = None
    upload_rate: int | None = None
    total_uploaded: int | None = None
    peers: int | None = None


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
    torrent_data = None
    if body.torrent_b64:
        try:
            torrent_data = base64.b64decode(body.torrent_b64)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="invalid torrent_b64 payload") from exc
    try:
        h = await rt.add_torrent(body.db_id, body.magnet_uri, body.save_path, torrent_data=torrent_data)
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


@router.post("/torrents/{db_id}/restore-from-disk", response_model=RuntimeHandleOut)
async def restore_torrent_from_disk(request: Request, db_id: int, save_path: str = Query(..., min_length=1)):
    rt = get_runtime(request)
    restore_fn = getattr(rt, "restore_from_disk", None)
    if restore_fn is None:
        raise HTTPException(status_code=501, detail="restore-from-disk not supported for this backend")
    try:
        h = await restore_fn(db_id, save_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if h is None:
        raise HTTPException(status_code=404, detail="no .torrent file on disk for this db_id")
    return RuntimeHandleOut.model_validate(h)


@router.get("/torrents/{db_id}/peers", response_model=list[TorrentPeerOut])
async def list_runtime_peers(request: Request, db_id: int):
    rt = get_runtime(request)
    rows = await rt.list_peers(db_id)
    return [TorrentPeerOut.model_validate(r) for r in rows]


@router.get("/torrents/{db_id}/debug")
async def debug_runtime_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    debug_fn = getattr(rt, "debug_torrent", None)
    if debug_fn is None:
        raise HTTPException(status_code=501, detail="debug not supported for this backend")
    data = await debug_fn(db_id)
    if data is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return data


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
async def remove_runtime_torrent(
    request: Request,
    db_id: int,
    delete_files: bool = Query(False, description="Удалить скачанные файлы с диска"),
    save_path: str | None = Query(None, description="Папка сохранения (для удаления файлов без handle)"),
    display_name: str | None = Query(None, description="Имя раздачи (для удаления файлов без handle)"),
):
    rt = get_runtime(request)
    ok = await rt.remove(
        db_id,
        delete_files=delete_files,
        save_path=save_path,
        display_name=display_name,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
