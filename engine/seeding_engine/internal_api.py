import asyncio
import base64
import hmac
import os
import shutil
import tarfile
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


def _expected_token() -> str:
    return os.getenv("SEEDING_ENGINE_API_TOKEN", "").strip()


async def require_token(x_engine_token: str | None = Header(default=None)) -> None:
    """Защита внутреннего API общим токеном (оркестратор ↔ движок).

    Если SEEDING_ENGINE_API_TOKEN не задан — проверка отключена (обратная совместимость).
    Сравнение постоянного времени, чтобы не утекал токен по таймингу."""
    expected = _expected_token()
    if not expected:
        return
    if not x_engine_token or not hmac.compare_digest(x_engine_token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing engine token")


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(require_token)],
)


class TorrentRegisterIn(BaseModel):
    db_id: int = Field(..., ge=1)
    magnet_uri: str | None = None
    torrent_b64: str | None = None
    save_path: str = Field(..., min_length=1)


class TorrentImportIn(BaseModel):
    db_id: int = Field(..., ge=1)
    torrent_b64: str = Field(..., min_length=1)
    save_path: str = Field(..., min_length=1)
    src_content_path: str = Field(..., min_length=1)


class TorrentFileBytesOut(BaseModel):
    db_id: int
    torrent_b64: str


class StageRemoteIn(BaseModel):
    db_id: int = Field(..., ge=1)
    save_path: str = Field(..., min_length=1)
    torrent_b64: str = Field(..., min_length=1)
    content_total: int = Field(0, ge=0)


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
    name: str | None = None
    size: int | None = None
    downloaded: int | None = None
    num_seeds: int | None = None
    ratio: float | None = None
    eta: int | None = None
    added_time: int | None = None
    download_limit: int | None = None
    upload_limit: int | None = None
    private: bool | None = None


class TorrentFileOut(BaseModel):
    index: int
    path: str
    size: int
    downloaded: int
    progress: float
    priority: int


class TorrentTrackerOut(BaseModel):
    url: str
    tier: int = 0
    message: str = ""
    verified: bool = False
    num_peers: int = 0


class FilePrioritiesIn(BaseModel):
    priorities: dict[int, int]


class LimitsIn(BaseModel):
    download_limit: int | None = None
    upload_limit: int | None = None


class PrivateIn(BaseModel):
    enabled: bool | None = None  # None = автоопределение по флагу/passkey


class TrackerAddIn(BaseModel):
    url: str = Field(..., min_length=8)


class SessionLimitsIn(BaseModel):
    download_limit: int | None = None
    upload_limit: int | None = None


class NetSettingsIn(BaseModel):
    dht: bool | None = None
    pex: bool | None = None
    lsd: bool | None = None


class ImportDirectIn(BaseModel):
    source_url: str = Field(..., min_length=4)
    save_path: str = Field(..., min_length=1)
    torrent_b64: str = Field(..., min_length=1)


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


@router.get("/torrents/{db_id}/torrent-file", response_model=TorrentFileBytesOut)
async def get_torrent_file(request: Request, db_id: int):
    """Отдать сохранённый .torrent (для переноса раздачи на другой движок)."""
    rt = get_runtime(request)
    read_fn = getattr(rt, "read_torrent_file", None)
    if read_fn is None:
        raise HTTPException(status_code=501, detail="torrent-file not supported for this backend")
    data = await read_fn(db_id)
    if data is None:
        raise HTTPException(status_code=404, detail="no .torrent file on disk for this db_id")
    return TorrentFileBytesOut(db_id=db_id, torrent_b64=base64.b64encode(data).decode("ascii"))


@router.post("/torrents/import-local", response_model=RuntimeHandleOut)
async def import_local_torrent(request: Request, body: TorrentImportIn):
    """Импорт раздачи с другого движка одной машины: копирование контента из /media + recheck."""
    rt = get_runtime(request)
    import_fn = getattr(rt, "import_local", None)
    if import_fn is None:
        raise HTTPException(status_code=501, detail="import-local not supported for this backend")
    try:
        torrent_data = base64.b64decode(body.torrent_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="invalid torrent_b64 payload") from exc
    try:
        h = await import_fn(body.db_id, body.save_path, body.src_content_path, torrent_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeHandleOut.model_validate(h)


@router.get("/fs/exists")
async def fs_exists(request: Request, path: str = Query(..., min_length=1)):
    """Проверить, виден ли путь с этого движка (факт-проверка общего /media для авто-переноса)."""
    p = Path(path)
    try:
        exists = p.exists()
        is_dir = p.is_dir() if exists else False
    except OSError:
        exists, is_dir = False, False
    return {"path": path, "exists": exists, "is_dir": is_dir}


@router.get("/net/status")
async def net_status(request: Request):
    """Сетевой статус движка (для проверки связности/NAT при онбординге)."""
    rt = get_runtime(request)
    fn = getattr(rt, "net_status", None)
    data = await fn() if fn is not None else {}
    return data or {}


@router.get("/torrents/{db_id}/content")
async def stream_content(request: Request, db_id: int):
    """Отдать контент раздачи tar-потоком (источник сетевого переноса между машинами)."""
    rt = get_runtime(request)
    loc_fn = getattr(rt, "content_location", None)
    if loc_fn is None:
        raise HTTPException(status_code=501, detail="content streaming not supported for this backend")
    loc = await loc_fn(db_id)
    if loc is None:
        raise HTTPException(status_code=404, detail="torrent not in runtime")
    path, total = loc
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="content not found on disk")

    async def gen():
        loop = asyncio.get_running_loop()
        rfd, wfd = os.pipe()

        def write_tar() -> None:
            with os.fdopen(wfd, "wb") as wf, tarfile.open(fileobj=wf, mode="w|") as tf:
                tf.add(str(p), arcname=p.name)

        task = loop.run_in_executor(None, write_tar)
        rf = os.fdopen(rfd, "rb")
        try:
            while True:
                chunk = await loop.run_in_executor(None, rf.read, 1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            rf.close()
            await task

    return StreamingResponse(
        gen(),
        media_type="application/x-tar",
        headers={"X-Content-Total": str(total)},
    )


@router.post("/torrents/stage-remote")
async def stage_remote_import(request: Request, body: StageRemoteIn):
    """Сохранить метаданные перед приёмом потока контента (сетевой перенос, приёмник)."""
    rt = get_runtime(request)
    fn = getattr(rt, "stage_import", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="remote import not supported for this backend")
    try:
        torrent_data = base64.b64decode(body.torrent_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="invalid torrent_b64 payload") from exc
    fn(body.db_id, body.save_path, torrent_data, body.content_total)
    return {"ok": True}


@router.post("/torrents/{db_id}/import-remote", response_model=RuntimeHandleOut)
async def import_remote_torrent(request: Request, db_id: int):
    """Принять tar-поток контента с движка другой машины (тело запроса) + recheck."""
    rt = get_runtime(request)
    pop_fn = getattr(rt, "pop_staged_import", None)
    import_fn = getattr(rt, "import_remote", None)
    if pop_fn is None or import_fn is None:
        raise HTTPException(status_code=501, detail="remote import not supported for this backend")
    staged = pop_fn(db_id)
    if staged is None:
        raise HTTPException(status_code=409, detail="import not staged; call stage-remote first")

    async def src_iter():
        async for chunk in request.stream():
            yield chunk

    try:
        h = await import_fn(
            db_id,
            staged["save_path"],
            staged["torrent_data"],
            staged["content_total"],
            src_iter(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeHandleOut.model_validate(h)


@router.get("/torrents/{db_id}/content-manifest")
async def content_manifest(request: Request, db_id: int):
    """Манифест контента (root + файлы и размеры) для возобновляемого сетевого переноса."""
    rt = get_runtime(request)
    fn = getattr(rt, "content_manifest", None)
    data = await fn(db_id) if fn is not None else None
    if data is None:
        raise HTTPException(status_code=404, detail="torrent not in runtime")
    return data


@router.get("/torrents/{db_id}/content-file")
async def content_file(
    request: Request,
    db_id: int,
    path: str = Query(..., min_length=1),
    range: str | None = Header(default=None),
):
    """Отдать один файл контента с поддержкой Range (докачка с нужного смещения)."""
    rt = get_runtime(request)
    fn = getattr(rt, "content_file_path", None)
    fp = await fn(db_id, path) if fn is not None else None
    if fp is None or not Path(fp).is_file():
        raise HTTPException(status_code=404, detail="content file not found")
    fp = Path(fp)
    size = fp.stat().st_size
    start = 0
    end = size - 1
    status = 200
    if range and range.startswith("bytes="):
        spec = range[len("bytes="):].split(",")[0].strip()
        lo, _, hi = spec.partition("-")
        try:
            if lo:
                start = int(lo)
            if hi:
                end = int(hi)
        except ValueError:
            start, end = 0, size - 1
        start = max(0, min(start, size))
        end = min(end, size - 1)
        status = 206
    length = max(0, end - start + 1)

    async def gen():
        loop = asyncio.get_running_loop()
        remaining = length
        with open(fp, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = await loop.run_in_executor(None, f.read, min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Content-Length": str(length), "Accept-Ranges": "bytes"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(gen(), status_code=status, media_type="application/octet-stream", headers=headers)


@router.get("/torrents/{db_id}/import-file-size")
async def import_file_size(
    request: Request,
    db_id: int,
    save_path: str = Query(..., min_length=1),
    root: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
):
    """Сколько байт файла уже принято (offset для возобновления приёма)."""
    rt = get_runtime(request)
    fn = getattr(rt, "import_file_size", None)
    size = fn(save_path, root, path) if fn is not None else 0
    return {"size": int(size)}


@router.post("/torrents/{db_id}/import-file")
async def import_file(
    request: Request,
    db_id: int,
    save_path: str = Query(..., min_length=1),
    root: str = Query(..., min_length=1),
    path: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
):
    """Дописать файл приёмника с offset (тело запроса — байты, возобновляемый приём)."""
    rt = get_runtime(request)
    fn = getattr(rt, "import_file_write", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="resumable import not supported")

    async def src_iter():
        async for chunk in request.stream():
            yield chunk

    try:
        written = await fn(save_path, root, path, offset, src_iter())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"written": int(written)}


@router.get("/peer-check")
async def peer_check(request: Request, url: str = Query(..., min_length=4)):
    """Видит ли ЭТОТ движок другой движок напрямую (для авто-выбора direct-переноса)."""
    rt = get_runtime(request)
    fn = getattr(rt, "peer_reachable", None)
    if fn is None:
        return {"reachable": False, "error": "not supported"}
    return await fn(url)


@router.post("/torrents/{db_id}/import-direct", response_model=RuntimeHandleOut)
async def import_direct(request: Request, db_id: int, body: ImportDirectIn):
    """Принять раздачу, скачав контент НАПРЯМУЮ у движка-источника (минуя оркестратор)."""
    rt = get_runtime(request)
    fn = getattr(rt, "import_direct", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="direct import not supported for this backend")
    try:
        torrent_data = base64.b64decode(body.torrent_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="invalid torrent_b64 payload") from exc
    try:
        h = await fn(db_id, body.save_path, torrent_data, body.source_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeHandleOut.model_validate(h)


@router.post("/torrents/{db_id}/import-finalize", response_model=RuntimeHandleOut)
async def import_finalize(request: Request, db_id: int):
    """Завершить возобновляемый импорт: add + recheck по собранному на диске контенту."""
    rt = get_runtime(request)
    fn = getattr(rt, "import_finalize", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="resumable import not supported")
    h = await fn(db_id)
    if h is None:
        raise HTTPException(status_code=409, detail="import not staged; call stage-remote first")
    return RuntimeHandleOut.model_validate(h)


@router.get("/torrents/{db_id}/migrate-progress")
async def get_migrate_progress(request: Request, db_id: int):
    """Прогресс копирования контента при импорте раздачи (перенос с другого движка)."""
    rt = get_runtime(request)
    fn = getattr(rt, "migrate_progress", None)
    prog = fn(db_id) if fn is not None else None
    if not prog:
        return {"active": False}
    return {"active": True, **prog}


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


@router.get("/torrents/{db_id}/files", response_model=list[TorrentFileOut])
async def list_runtime_files(request: Request, db_id: int):
    rt = get_runtime(request)
    rows = await rt.list_files(db_id)
    return [TorrentFileOut.model_validate(r) for r in rows]


@router.post("/torrents/{db_id}/files/priorities")
async def set_runtime_file_priorities(request: Request, db_id: int, body: FilePrioritiesIn):
    rt = get_runtime(request)
    ok = await rt.set_file_priorities(db_id, body.priorities)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in runtime or metadata not ready")
    return {"ok": True}


@router.get("/torrents/{db_id}/trackers", response_model=list[TorrentTrackerOut])
async def list_runtime_trackers(request: Request, db_id: int):
    rt = get_runtime(request)
    rows = await rt.list_trackers(db_id)
    return [TorrentTrackerOut.model_validate(r) for r in rows]


@router.post("/torrents/{db_id}/recheck")
async def recheck_runtime_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    ok = await rt.recheck(db_id)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return {"ok": True}


@router.post("/torrents/{db_id}/reannounce")
async def reannounce_runtime_torrent(request: Request, db_id: int):
    rt = get_runtime(request)
    ok = await rt.reannounce(db_id)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return {"ok": True}


@router.post("/torrents/{db_id}/limits", response_model=RuntimeHandleOut)
async def set_runtime_limits(request: Request, db_id: int, body: LimitsIn):
    rt = get_runtime(request)
    h = await rt.set_limits(db_id, body.download_limit, body.upload_limit)
    if h is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return RuntimeHandleOut.model_validate(h)


@router.post("/torrents/{db_id}/private", response_model=RuntimeHandleOut)
async def set_runtime_private(request: Request, db_id: int, body: PrivateIn):
    rt = get_runtime(request)
    h = await rt.set_private(db_id, body.enabled)
    if h is None:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    return RuntimeHandleOut.model_validate(h)


@router.get("/session/stats")
async def session_stats(request: Request):
    rt = get_runtime(request)
    stats = await rt.session_stats()
    # Свободное место на томе движка — чтобы UI понимал, влезет ли контент.
    try:
        root = os.getenv("SEEDING_DATA_ROOT", "/data")
        usage = shutil.disk_usage(root)
        if isinstance(stats, dict):
            stats["disk_total"] = int(usage.total)
            stats["disk_free"] = int(usage.free)
    except OSError:
        pass
    return stats


@router.post("/session/limits")
async def set_session_limits(request: Request, body: SessionLimitsIn):
    rt = get_runtime(request)
    return await rt.set_session_limits(body.download_limit, body.upload_limit)


@router.get("/session/net-settings")
async def get_session_net(request: Request):
    rt = get_runtime(request)
    return await rt.net_settings()


@router.post("/session/net-settings")
async def set_session_net(request: Request, body: NetSettingsIn):
    rt = get_runtime(request)
    return await rt.set_net(body.dht, body.pex, body.lsd)


@router.post("/torrents/{db_id}/trackers", response_model=list[TorrentTrackerOut])
async def add_runtime_tracker(request: Request, db_id: int, body: TrackerAddIn):
    rt = get_runtime(request)
    ok = await rt.add_tracker(db_id, body.url)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent not in engine runtime")
    rows = await rt.list_trackers(db_id)
    return [TorrentTrackerOut.model_validate(r) for r in rows]


@router.delete("/torrents/{db_id}/trackers", response_model=list[TorrentTrackerOut])
async def remove_runtime_tracker(request: Request, db_id: int, url: str = Query(..., min_length=8)):
    rt = get_runtime(request)
    ok = await rt.remove_tracker(db_id, url)
    if not ok:
        raise HTTPException(status_code=404, detail="torrent or tracker not found")
    rows = await rt.list_trackers(db_id)
    return [TorrentTrackerOut.model_validate(r) for r in rows]


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
