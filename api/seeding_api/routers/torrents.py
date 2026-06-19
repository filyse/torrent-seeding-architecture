import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from seeding_db.engine_registry import normalize_save_path
from seeding_db.models import TorrentStatus
from seeding_db.repository import MigrationRepository, TorrentRepository

from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.migrate import cancel_migration, run_migration, set_progress
from seeding_api.runtime_sync import merge_runtime_into_row
from seeding_api.schemas import (
    BatchUploadItem,
    BatchUploadResult,
    BulkIdsIn,
    BulkLabelIn,
    FilePrioritiesIn,
    LimitsIn,
    PrivateIn,
    TorrentCreate,
    TorrentDetailOut,
    TorrentFacetsOut,
    TorrentFileOut,
    TorrentOut,
    TorrentPageOut,
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


def _resolve_target(pool, engine_id: str | None, save_path: str | None) -> tuple[str, str]:
    """Куда класть торрент: либо явный engine_id (UI выбирает движок, путь = его storage_prefix),
    либо save_path со строгим матчингом префикса. Возвращает (engine_id, save_path).

    Никакого молчаливого дефолта: если путь относительный или не принадлежит ни одному движку —
    явная 422, чтобы торрент не «уехал» не туда."""
    eid = (engine_id or "").strip()
    if eid:
        spec = pool.spec(eid)
        if spec is None:
            raise HTTPException(status_code=422, detail=f"unknown engine_id: {eid}")
        return spec.id, spec.normalized_prefix()
    sp = (save_path or "").strip()
    if not sp:
        raise HTTPException(status_code=422, detail="engine_id or save_path is required")
    norm = normalize_save_path(sp)
    if not norm.startswith("/"):
        raise HTTPException(
            status_code=422,
            detail="save_path must be absolute (start with '/') or pass engine_id",
        )
    matched = pool.match_engine_id(norm)
    if matched is None:
        prefixes = ", ".join(s.normalized_prefix() for s in pool.specs)
        raise HTTPException(
            status_code=422,
            detail=f"save_path '{norm}' does not belong to any engine (prefixes: {prefixes}); pass engine_id",
        )
    return matched, norm


@router.get("", response_model=TorrentPageOut)
async def list_torrents(
    session: DbSession,
    pool: EnginePoolDep,
    q: str | None = Query(None, description="Поиск по имени/метке/hash"),
    status: str | None = Query(None, description="Фильтр по статусу"),
    label: str | None = Query(None, description="Фильтр по метке"),
    engine_id: str | None = Query(None, description="Фильтр по движку"),
    state: str | None = Query(
        None, description="active|traffic|peers|idle|incomplete|error (по активности)"
    ),
    sort: str = Query("name", description="name|added|up|down|peers|uploaded|ratio|size|progress"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Постраничный список раздач. Фильтр/сортировка/пагинация — на стороне БД, поэтому
    масштабируется на тысячи раздач: рантайм с движков тянется ТОЛЬКО для текущей страницы,
    и одним батч-запросом на движок (а не по торренту). Сорт/фильтр по «живым» полям
    (отдача/пиры/…) работает по снимку, который пишет фоновый воркер."""
    repo = TorrentRepository(session)
    rows, total = await repo.list_page(
        q=q,
        status=status,
        label=label,
        engine_id=engine_id,
        state=state,
        sort=sort,
        limit=limit,
        offset=offset,
    )

    # Рантайм только для движков текущей страницы: один список с движка, параллельно.
    engine_ids = {row.engine_id for row in rows if row.engine_id}

    async def _fetch(eid: str) -> tuple[str, dict[int, dict]]:
        try:
            return eid, await pool.client_for(eid).list_runtime()
        except (httpx.HTTPError, KeyError):
            return eid, {}

    fetched = await asyncio.gather(*(_fetch(eid) for eid in engine_ids))
    runtime_by_engine: dict[str, dict[int, dict]] = dict(fetched)

    items: list[TorrentDetailOut] = []
    for row in rows:
        runtime = runtime_by_engine.get(row.engine_id, {}).get(row.id)
        merged = await merge_runtime_into_row(repo, row, runtime)
        data = TorrentOut.model_validate(row).model_dump()
        data["status"] = merged
        data["runtime"] = runtime
        items.append(TorrentDetailOut.model_validate(data))
    return TorrentPageOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/facets", response_model=TorrentFacetsOut)
async def torrents_facets(session: DbSession):
    """Счётчики для подписи количества у вариантов фильтров (статус/метка/движок/состояние).
    Считаются по БД (снимок рантайма пишет фоновый воркер), поэтому дёшево и масштабируемо."""
    return await TorrentRepository(session).facets()


@router.post("", response_model=TorrentOut, status_code=201)
async def create_torrent(body: TorrentCreate, session: DbSession, pool: EnginePoolDep):
    if not body.magnet_uri.startswith("magnet:"):
        raise HTTPException(status_code=422, detail="magnet_uri must start with magnet:")
    engine_id, sp = _resolve_target(pool, body.engine_id, body.save_path)
    repo = TorrentRepository(session)
    row = await repo.create(
        display_name=body.display_name,
        save_path=sp,
        magnet_uri=body.magnet_uri,
        engine_id=engine_id,
        label=body.label.strip(),
    )
    await session.flush()
    await session.refresh(row)
    try:
        await pool.client_for(engine_id).register_torrent(row.id, body.magnet_uri, row.save_path)
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
    save_path: str = Form(""),
    engine_id: str = Form(""),
    display_name: str = Form(""),
    label: str = Form(""),
    seed_mode: bool = Form(False),
):
    filename = (torrent_file.filename or "").strip()
    if not filename.lower().endswith(".torrent"):
        raise HTTPException(status_code=422, detail="only .torrent files are supported")
    payload = await torrent_file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="torrent file is empty")

    engine_id, sp = _resolve_target(pool, engine_id, save_path)
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
        await pool.client_for(engine_id).register_torrent_file(
            row.id, payload, row.save_path, seed_mode=seed_mode
        )
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
    save_path: str = Form(""),
    engine_id: str = Form(""),
    label: str = Form(""),
):
    """Мульти-загрузка: несколько .torrent за один запрос. Отчёт по каждому файлу;
    сбой одного файла не отменяет остальные (частичный успех допустим)."""
    if not torrent_files:
        raise HTTPException(status_code=422, detail="no files provided")

    engine_id, sp = _resolve_target(pool, engine_id, save_path)
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
    engine_id, sp = _resolve_target(pool, body.engine_id, body.save_path)
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


@router.post("/bulk/label")
async def bulk_label(body: BulkLabelIn, session: DbSession):
    """Массовое назначение метки выбранным раздачам (пустая строка — снять метку)."""
    repo = TorrentRepository(session)
    label = body.label.strip()
    rows = await repo.get_by_ids(body.ids)
    ok = 0
    for row in rows:
        await repo.update_label(row.id, label)
        ok += 1
    return {"ok": ok, "label": label}


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


@router.post("/{torrent_id}/migrate")
async def migrate_torrent(
    torrent_id: int,
    request: Request,
    session: DbSession,
    pool: EnginePoolDep,
    engine_id: str = Query(..., min_length=1, description="Целевой движок переноса"),
    transport: str = Query("auto", description="auto|media|http — способ передачи контента"),
):
    """Перенести раздачу на другой движок одной машины (копия через /media + recheck).

    Запускает фоновый перенос и сразу возвращает статус `migrating`. Контент источника
    удаляется только после подтверждённой полной копии на цели."""
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")

    target_id = engine_id.strip()
    target_spec = pool.spec(target_id)
    if target_spec is None:
        raise HTTPException(status_code=422, detail=f"unknown engine_id: {target_id}")
    if target_id == row.engine_id:
        raise HTTPException(status_code=422, detail="target engine equals source engine")
    if row.status == TorrentStatus.migrating.value:
        raise HTTPException(status_code=409, detail="torrent is already migrating")

    source_spec = pool.spec(row.engine_id)
    if source_spec is None:
        raise HTTPException(status_code=422, detail=f"unknown source engine: {row.engine_id}")
    source_media = source_spec.normalized_media_path()
    source_url = source_spec.url
    requested = transport.strip().lower()
    mode = requested
    if mode not in ("auto", "media", "http", "direct"):
        raise HTTPException(status_code=422, detail="transport must be auto|media|http|direct")

    # Имя контента нужно и для пути в /media, и для tar-стрима, и для факт-проверки видимости.
    try:
        runtime = await pool.client_for(row.engine_id).runtime_snapshot(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="source engine unavailable") from exc
    name = (runtime or {}).get("name") if runtime else None
    if not name:
        raise HTTPException(
            status_code=409,
            detail="torrent is not active on source engine; cannot migrate",
        )

    probe_path = f"{source_media}/{name}" if source_media else ""
    target_client = pool.client_for(target_id)
    if mode == "auto":
        # Выбор по факту: общий /media (быстрее всего) → прямой pull движок→движок → проксируемый http.
        mode = "http"
        if probe_path:
            try:
                if await target_client.path_exists(probe_path):
                    mode = "media"
            except httpx.HTTPError:
                pass
        if mode != "media":
            try:
                if await target_client.peer_check(source_url):
                    mode = "direct"
            except httpx.HTTPError:
                pass
    if mode == "media" and not source_media:
        raise HTTPException(
            status_code=422,
            detail=(
                f"source engine '{row.engine_id}' has no media_path; "
                "use transport=http for cross-machine migration"
            ),
        )
    if requested == "direct":
        # Явный direct: убедимся, что приёмник реально достучится до источника.
        try:
            reachable = await target_client.peer_check(source_url)
        except httpx.HTTPError:
            reachable = False
        if not reachable:
            raise HTTPException(
                status_code=422,
                detail="target engine cannot reach source engine directly; use transport=http",
            )

    src_content_path = probe_path if mode == "media" else ""
    source_save_path = source_spec.normalized_prefix()
    target_save_path = target_spec.normalized_prefix()

    await repo.update_status(torrent_id, TorrentStatus.migrating.value)
    await session.commit()

    _launch_migration(
        request, pool,
        torrent_id=torrent_id,
        source_engine_id=row.engine_id,
        target_engine_id=target_id,
        source_save_path=source_save_path,
        target_save_path=target_save_path,
        src_content_path=src_content_path,
        display_name=row.display_name,
        transport=mode,
        source_url=source_url,
        resume=False,
    )
    return {
        "id": torrent_id,
        "status": TorrentStatus.migrating.value,
        "source_engine_id": row.engine_id,
        "engine_id": target_id,
        "transport": mode,
    }


def _launch_migration(
    request: Request,
    pool,
    *,
    torrent_id: int,
    source_engine_id,
    target_engine_id: str,
    source_save_path: str,
    target_save_path: str,
    src_content_path: str,
    display_name: str,
    transport: str,
    source_url: str,
    resume: bool,
) -> None:
    """Запустить фоновую задачу переноса и зарегистрировать её в app.state."""
    progress_store = getattr(request.app.state, "migrate_progress", None)
    if progress_store is None:
        progress_store = {}
        request.app.state.migrate_progress = progress_store
    set_progress(
        progress_store, torrent_id, "preparing",
        message=f"{'resume' if resume else 'start'} → {target_engine_id}",
    )
    task = asyncio.create_task(
        run_migration(
            request.app.state.session_factory,
            pool,
            torrent_id=torrent_id,
            source_engine_id=source_engine_id,
            target_engine_id=target_engine_id,
            source_save_path=source_save_path,
            target_save_path=target_save_path,
            src_content_path=src_content_path,
            display_name=display_name,
            transport=transport,
            source_url=source_url,
            progress_store=progress_store,
            resume=resume,
        )
    )
    tasks = getattr(request.app.state, "migrate_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.migrate_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("/{torrent_id}/migrate/resume")
async def resume_migration(torrent_id: int, request: Request, session: DbSession, pool: EnginePoolDep):
    """Возобновить прерванный/неуспешный перенос с места обрыва (без копии с нуля)."""
    mrepo = MigrationRepository(session)
    job = await mrepo.get(torrent_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no migration job to resume")
    if job.state == "running":
        raise HTTPException(status_code=409, detail="migration already running")
    src_spec = pool.spec(job.source_engine_id)
    if pool.spec(job.target_engine_id) is None or src_spec is None:
        raise HTTPException(status_code=422, detail="engine of this migration is no longer known")

    repo = TorrentRepository(session)
    await repo.update_status(torrent_id, TorrentStatus.migrating.value)
    await session.commit()

    _launch_migration(
        request, pool,
        torrent_id=torrent_id,
        source_engine_id=job.source_engine_id,
        target_engine_id=job.target_engine_id,
        source_save_path=job.source_save_path,
        target_save_path=job.target_save_path,
        src_content_path=job.src_content_path,
        display_name=job.display_name,
        transport=job.transport,
        source_url=src_spec.url,
        resume=True,
    )
    return {"id": torrent_id, "status": TorrentStatus.migrating.value, "resumed": True}


@router.post("/{torrent_id}/migrate/cancel")
async def cancel_migration_endpoint(
    torrent_id: int, request: Request, session: DbSession, pool: EnginePoolDep
):
    """Полностью отменить перенос: удалить частичную копию на цели, вернуть источник."""
    ok = await cancel_migration(
        request.app.state.session_factory, pool, torrent_id=torrent_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="no migration job to cancel")
    store = getattr(request.app.state, "migrate_progress", None)
    if store is not None:
        store.pop(torrent_id, None)
    return {"id": torrent_id, "cancelled": True}


@router.get("/{torrent_id}/migrate-status")
async def migrate_status(torrent_id: int, request: Request, session: DbSession):
    """Текущий прогресс переноса для опроса из UI (фаза + проценты + возобновляемость)."""
    mrepo = MigrationRepository(session)
    job = await mrepo.get(torrent_id)
    store = getattr(request.app.state, "migrate_progress", None)
    snap = store.get(torrent_id) if store else None
    if snap is not None:
        active = snap.get("phase") not in ("done", "error")
        resumable = (not active) and bool(job) and job.state == "failed"
        return {
            "id": torrent_id, "active": active, "resumable": resumable,
            "attempts": job.attempts if job else 0,
            "transport": job.transport if job else None, **snap,
        }
    # Прогресса в памяти нет (например, после перезапуска оркестратора) — берём из БД-джоба.
    if job is not None:
        active = job.state == "running"
        pct = (job.copied / job.total) if job.total else None
        return {
            "id": torrent_id,
            "active": active,
            "resumable": job.state == "failed",
            "phase": job.phase,
            "progress": pct,
            "copied": job.copied,
            "total": job.total,
            "attempts": job.attempts,
            "transport": job.transport,
            "message": job.last_error or None,
        }
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    migrating = bool(row and row.status == TorrentStatus.migrating.value)
    return {
        "id": torrent_id,
        "active": migrating,
        "resumable": False,
        "phase": "migrating" if migrating else "idle",
        "progress": None,
        "message": None,
    }


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


@router.post("/{torrent_id}/private", response_model=TorrentDetailOut)
async def set_torrent_private(
    torrent_id: int, body: PrivateIn, session: DbSession, pool: EnginePoolDep
):
    repo = TorrentRepository(session)
    row = await repo.get_by_id(torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    try:
        runtime = await pool.client_for_row(row).set_private(torrent_id, body.enabled)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    if runtime is None:
        raise HTTPException(status_code=409, detail="torrent not in runtime")
    status = await merge_runtime_into_row(repo, row, runtime)
    data = TorrentOut.model_validate(row).model_dump()
    data["status"] = status
    data["runtime"] = runtime
    return TorrentDetailOut.model_validate(data)


@router.post("/maintenance/reapply-private")
async def reapply_private_all(session: DbSession, pool: EnginePoolDep):
    """Прогнать автоопределение приватности по всем раздачам и заглушить DHT/PEX/LSD
    там, где трекер приватный (флаг private или passkey)."""
    repo = TorrentRepository(session)
    rows = await repo.list_all()
    changed = 0
    private = 0
    errors = 0
    for row in rows:
        try:
            runtime = await pool.client_for_row(row).set_private(row.id, None)
        except httpx.HTTPError:
            errors += 1
            continue
        if runtime is None:
            continue
        changed += 1
        if runtime.get("private"):
            private += 1
    return {"checked": len(rows), "applied": changed, "private": private, "errors": errors}


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
