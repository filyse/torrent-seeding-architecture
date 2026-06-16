import logging
import os

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from seeding_db.models import TorrentStatus
from seeding_db.repository import TorrentRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.runtime_sync import merge_runtime_into_row
from seeding_api.schemas import (
    BatchUploadItem,
    BatchUploadResult,
    BulkIdsIn,
    FilePrioritiesIn,
    LimitsIn,
    TorrentCreate,
    TorrentDetailOut,
    TorrentFileOut,
    TorrentOut,
    TorrentPatch,
    TorrentTrackerOut,
    TorrentUrlCreate,
    TrackerAddIn,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _require_engine_for_delete() -> bool:
    """Если True — при ошибке HTTP к движку DELETE возвращает 502, строка в БД не трогается."""
    v = os.getenv("SEEDING_REQUIRE_ENGINE_FOR_DELETE", "").strip().lower()
    return v in ("1", "true", "yes")


@router.get("", response_model=list[TorrentDetailOut])
async def list_torrents(session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    rows = await repo.list_all()
    out: list[TorrentDetailOut] = []
    for row in rows:
        runtime = None
        try:
            runtime = await pool.client_for_row(row).runtime_snapshot(row.id)
        except httpx.HTTPError:
            runtime = None
        status = await merge_runtime_into_row(repo, row, runtime)
        data = TorrentOut.model_validate(row).model_dump()
        data["status"] = status
        data["runtime"] = runtime
        out.append(TorrentDetailOut.model_validate(data))
    return out


@router.post("", response_model=TorrentOut, status_code=201)
async def create_torrent(body: TorrentCreate, session: DbSession, pool: EnginePoolDep):
    if not body.magnet_uri.startswith("magnet:"):
        raise HTTPException(status_code=422, detail="magnet_uri must start with magnet:")
    engine_id = pool.resolve_engine_id(body.save_path)
    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=body.display_name,
        save_path=body.save_path,
        magnet_uri=body.magnet_uri,
        engine_id=engine_id,
        label=body.label.strip(),
    )
    await session.flush()
    await session.refresh(row)
    try:
        await pool.client_for(engine_id).register_torrent(row.id, body.magnet_uri, body.save_path)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(row.id, TorrentStatus.downloading.value)
    await session.refresh(row)
    return row


@router.post("/upload", response_model=TorrentOut, status_code=201)
async def upload_torrent_file(
    session: DbSession,
    pool: EnginePoolDep,
    torrent_file: UploadFile = File(...),
    save_path: str = Form(...),
    display_name: str = Form(""),
    label: str = Form(""),
):
    filename = (torrent_file.filename or "").strip()
    if not filename.lower().endswith(".torrent"):
        raise HTTPException(status_code=422, detail="only .torrent files are supported")
    if not save_path.strip():
        raise HTTPException(status_code=422, detail="save_path is required")
    payload = await torrent_file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="torrent file is empty")

    sp = save_path.strip()
    engine_id = pool.resolve_engine_id(sp)
    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=display_name.strip() or filename,
        save_path=sp,
        magnet_uri=None,
        engine_id=engine_id,
        label=label.strip(),
    )
    await session.flush()
    await session.refresh(row)
    try:
        await pool.client_for(engine_id).register_torrent_file(row.id, payload, row.save_path)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.update_status(row.id, TorrentStatus.downloading.value)
    await session.refresh(row)
    return row


@router.post("/upload-batch", response_model=BatchUploadResult, status_code=201)
async def upload_torrent_files(
    session: DbSession,
    pool: EnginePoolDep,
    torrent_files: list[UploadFile] = File(...),
    save_path: str = Form(...),
    label: str = Form(""),
):
    """Мульти-загрузка: несколько .torrent за один запрос. Отчёт по каждому файлу;
    сбой одного файла не отменяет остальные (частичный успех допустим)."""
    if not save_path.strip():
        raise HTTPException(status_code=422, detail="save_path is required")
    if not torrent_files:
        raise HTTPException(status_code=422, detail="no files provided")

    sp = save_path.strip()
    engine_id = pool.resolve_engine_id(sp)
    repo = TorrentRepository(session)
    client = pool.client_for(engine_id)

    items: list[BatchUploadItem] = []
    ok = 0
    for uf in torrent_files:
        filename = (uf.filename or "").strip()
        try:
            if not filename.lower().endswith(".torrent"):
                raise ValueError("only .torrent files are supported")
            payload = await uf.read()
            if not payload:
                raise ValueError("torrent file is empty")

            display_name = filename[: -len(".torrent")] or filename
            row = await repo.create(
                display_name=display_name,
                save_path=sp,
                magnet_uri=None,
                engine_id=engine_id,
                label=label.strip(),
            )
            await session.flush()
            await session.refresh(row)
            try:
                await client.register_torrent_file(row.id, payload, row.save_path)
            except (httpx.HTTPError, ValueError):
                # не оставляем «осиротевшую» строку в БД, если движок не принял файл
                await repo.delete(row.id)
                raise
            await repo.update_status(row.id, TorrentStatus.downloading.value)
            items.append(
                BatchUploadItem(filename=filename, ok=True, id=row.id, display_name=display_name)
            )
            ok += 1
        except (ValueError, httpx.HTTPError) as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            items.append(BatchUploadItem(filename=filename or "(без имени)", ok=False, error=detail))

    return BatchUploadResult(total=len(torrent_files), ok=ok, failed=len(torrent_files) - ok, items=items)


@router.post("/url", response_model=TorrentOut, status_code=201)
async def create_torrent_from_url(body: TorrentUrlCreate, session: DbSession, pool: EnginePoolDep):
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url must be http or https")
    sp = body.save_path.strip()
    engine_id = pool.resolve_engine_id(sp)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch torrent: {exc}") from exc
    if not payload or len(payload) < 20:
        raise HTTPException(status_code=422, detail="downloaded file is empty or too small")
    if not payload.lstrip().startswith(b"d"):
        raise HTTPException(status_code=422, detail="url did not return a valid .torrent file")

    name = body.display_name.strip()
    if not name:
        from urllib.parse import unquote, urlparse

        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1]) if path else "torrent"
        if name.lower().endswith(".torrent"):
            name = name[: -len(".torrent")]

    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=name,
        save_path=sp,
        magnet_uri=None,
        engine_id=engine_id,
        label=body.label.strip(),
    )
    await session.flush()
    await session.refresh(row)
    try:
        await pool.client_for(engine_id).register_torrent_file(row.id, payload, row.save_path)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.update_status(row.id, TorrentStatus.downloading.value)
    await session.refresh(row)
    return row


@router.post("/bulk/pause")
async def bulk_pause(body: BulkIdsIn, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    rows = await repo.get_by_ids(body.ids)
    ok, fail = 0, 0
    for row in rows:
        try:
            await pool.client_for_row(row).pause(row.id)
            await repo.update_status(row.id, TorrentStatus.paused.value)
            ok += 1
        except httpx.HTTPError:
            fail += 1
    return {"ok": ok, "fail": fail}


@router.post("/bulk/resume")
async def bulk_resume(body: BulkIdsIn, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    rows = await repo.get_by_ids(body.ids)
    ok, fail = 0, 0
    for row in rows:
        try:
            await pool.client_for_row(row).resume(row.id)
            await repo.update_status(row.id, TorrentStatus.downloading.value)
            ok += 1
        except httpx.HTTPError:
            fail += 1
    return {"ok": ok, "fail": fail}


@router.post("/bulk/delete")
async def bulk_delete(
    body: BulkIdsIn,
    session: DbSession,
    pool: EnginePoolDep,
    delete_files: bool = Query(False),
):
    repo = TorrentRepository(session)
    rows = await repo.get_by_ids(body.ids)
    ok, fail = 0, 0
    for row in rows:
        try:
            await pool.client_for_row(row).remove_from_runtime(
                row.id,
                delete_files=delete_files,
                save_path=row.save_path,
                display_name=row.display_name,
            )
        except httpx.HTTPError:
            if _require_engine_for_delete():
                fail += 1
                continue
        await repo.delete(row.id)
        ok += 1
    return {"ok": ok, "fail": fail}


@router.get("/{torrent_id}", response_model=TorrentDetailOut)
async def get_torrent(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    engine = pool.client_for_row(row)
    try:
        runtime = await engine.runtime_snapshot(torrent_id)
    except httpx.HTTPError:
        runtime = None
    if runtime and row.info_hash is None:
        ih = runtime.get("info_hash")
        if isinstance(ih, str) and ih and ih != "0" * 40:
            await repo.update_info_hash(torrent_id, ih)
            await session.refresh(row)
    status = await merge_runtime_into_row(repo, row, runtime)
    peer_list: list = []
    if runtime is not None:
        try:
            peer_list = await engine.list_peers(torrent_id)
        except httpx.HTTPError:
            peer_list = []
    data = TorrentOut.model_validate(row).model_dump()
    data["status"] = status
    data["runtime"] = runtime
    data["peer_list"] = peer_list
    return TorrentDetailOut.model_validate(data)


@router.patch("/{torrent_id}", response_model=TorrentOut)
async def patch_torrent(torrent_id: int, body: TorrentPatch, session: DbSession):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    if body.label is not None:
        await repo.update_label(torrent_id, body.label.strip())
    if body.display_name is not None:
        row.display_name = body.display_name.strip()
        await session.flush()
    out = await repo.get_by_id(torrent_id)
    assert out is not None
    return out


@router.get("/{torrent_id}/files", response_model=list[TorrentFileOut])
async def list_torrent_files(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        files = await pool.client_for_row(row).list_files(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return [TorrentFileOut.model_validate(f) for f in files]


@router.post("/{torrent_id}/files/priorities")
async def set_torrent_file_priorities(
    torrent_id: int, body: FilePrioritiesIn, session: DbSession, pool: EnginePoolDep
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        ok = await pool.client_for_row(row).set_file_priorities(torrent_id, body.priorities)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not ok:
        raise HTTPException(status_code=409, detail="metadata not ready or torrent not in runtime")
    return {"ok": True}


@router.get("/{torrent_id}/trackers", response_model=list[TorrentTrackerOut])
async def list_torrent_trackers(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        trackers = await pool.client_for_row(row).list_trackers(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return [TorrentTrackerOut.model_validate(t) for t in trackers]


@router.post("/{torrent_id}/trackers", response_model=list[TorrentTrackerOut])
async def add_torrent_tracker(
    torrent_id: int, body: TrackerAddIn, session: DbSession, pool: EnginePoolDep
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        trackers = await pool.client_for_row(row).add_tracker(torrent_id, body.url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return [TorrentTrackerOut.model_validate(t) for t in trackers]


@router.delete("/{torrent_id}/trackers", response_model=list[TorrentTrackerOut])
async def remove_torrent_tracker(
    torrent_id: int,
    session: DbSession,
    pool: EnginePoolDep,
    url: str = Query(..., min_length=8),
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        trackers = await pool.client_for_row(row).remove_tracker(torrent_id, url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="torrent or tracker not found") from exc
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    return [TorrentTrackerOut.model_validate(t) for t in trackers]


@router.post("/{torrent_id}/recheck")
async def recheck_torrent(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        ok = await pool.client_for_row(row).recheck(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not ok:
        raise HTTPException(status_code=409, detail="torrent not in runtime")
    return {"ok": True}


@router.post("/{torrent_id}/reannounce")
async def reannounce_torrent(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        ok = await pool.client_for_row(row).reannounce(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if not ok:
        raise HTTPException(status_code=409, detail="torrent not in runtime")
    return {"ok": True}


@router.post("/{torrent_id}/limits", response_model=TorrentDetailOut)
async def set_torrent_limits(
    torrent_id: int, body: LimitsIn, session: DbSession, pool: EnginePoolDep
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        runtime = await pool.client_for_row(row).set_limits(
            torrent_id, body.download_limit, body.upload_limit
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if runtime is None:
        raise HTTPException(status_code=409, detail="torrent not in runtime")
    status = await merge_runtime_into_row(repo, row, runtime)
    data = TorrentOut.model_validate(row).model_dump()
    data["status"] = status
    data["runtime"] = runtime
    return TorrentDetailOut.model_validate(data)


@router.post("/{torrent_id}/pause", response_model=TorrentOut)
async def pause_torrent(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await pool.client_for_row(row).pause(torrent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise HTTPException(status_code=502, detail="engine error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(torrent_id, TorrentStatus.paused.value)
    out = await repo.get_by_id(torrent_id)
    assert out is not None
    return out


@router.post("/{torrent_id}/resume", response_model=TorrentOut)
async def resume_torrent(torrent_id: int, session: DbSession, pool: EnginePoolDep):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await pool.client_for_row(row).resume(torrent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise HTTPException(status_code=502, detail="engine error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    await repo.update_status(torrent_id, TorrentStatus.downloading.value)
    out = await repo.get_by_id(torrent_id)
    assert out is not None
    return out


@router.delete("/{torrent_id}", status_code=204)
async def delete_torrent(
    torrent_id: int,
    session: DbSession,
    pool: EnginePoolDep,
    delete_files: bool = Query(
        False,
        description="Удалить скачанные файлы с диска (иначе только запись в БД и рантайм)",
    ),
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        await pool.client_for_row(row).remove_from_runtime(
            torrent_id,
            delete_files=delete_files,
            save_path=row.save_path,
            display_name=row.display_name,
        )
    except httpx.HTTPError as exc:
        if _require_engine_for_delete():
            raise HTTPException(status_code=502, detail="engine unavailable") from exc
        log.warning(
            "delete torrent_id=%s: engine HTTP error, removing DB row anyway; "
            "runtime may be stale until engine restart "
            "(set SEEDING_REQUIRE_ENGINE_FOR_DELETE=1 to require engine and keep row on failure)",
            torrent_id,
            exc_info=True,
        )
    await repo.delete(torrent_id)
