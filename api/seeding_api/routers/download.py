"""Ticket на прямое скачивание файла с движка (байты не через CT400)."""

from __future__ import annotations

from pathlib import PurePosixPath

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from seeding_db.repository import TorrentRepository

from seeding_api.auth import Principal, require_auth
from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.upload_ticket import (
    download_enabled,
    download_ticket_secret,
    download_ticket_ttl,
    issue_download_ticket,
    normalize_download_path,
    resolve_upload_base_url,
    upload_base_urls,
    upload_relay_base_urls,
    upload_relay_enabled,
)

router = APIRouter(tags=["download"])


class FeaturesOut(BaseModel):
    enabled: bool
    relay_enabled: bool = False


class TicketIn(BaseModel):
    torrent_id: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=1024)


class TicketOut(BaseModel):
    ticket: str
    url: str
    relay_url: str | None = None
    filename: str
    size: int
    expires_in: int


def _features_on() -> bool:
    return download_enabled() and bool(download_ticket_secret()) and bool(upload_base_urls())


@router.get("/download/features", response_model=FeaturesOut)
async def download_features(_: Principal = Depends(require_auth)):
    return FeaturesOut(enabled=_features_on(), relay_enabled=upload_relay_enabled())


def _match_file(files: list[dict], path: str) -> dict | None:
    for item in files:
        raw = str(item.get("path") or "")
        try:
            if normalize_download_path(raw) == path:
                return item
        except ValueError:
            continue
    base = PurePosixPath(path).name
    hits = []
    for item in files:
        raw = str(item.get("path") or "")
        if PurePosixPath(raw.replace("\\", "/")).name == base:
            hits.append(item)
    return hits[0] if len(hits) == 1 else None


@router.post("/download/ticket", response_model=TicketOut)
async def create_download_ticket(
    body: TicketIn,
    session: DbSession,
    pool: EnginePoolDep,
    principal: Principal = Depends(require_auth),
):
    if not download_enabled():
        raise HTTPException(status_code=404, detail="file download is disabled")
    if not download_ticket_secret():
        raise HTTPException(status_code=503, detail="download ticket secret not configured")
    try:
        path = normalize_download_path(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = await TorrentRepository(session).get_by_id(body.torrent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="torrent not found")
    engine_id = (row.engine_id or "").strip()
    if not engine_id or pool.spec(engine_id) is None:
        raise HTTPException(status_code=409, detail="torrent has no engine")

    try:
        files = await pool.client_for_row(row).list_files(body.torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="engine unavailable") from exc
    match = _match_file(files, path)
    if match is None:
        raise HTTPException(status_code=404, detail="file not in torrent")
    size = int(match.get("size") or 0)

    direct = resolve_upload_base_url(engine_id, upload_base_urls())
    if not direct:
        raise HTTPException(
            status_code=503,
            detail=f"no download base URL for engine {engine_id}",
        )
    relay_base = resolve_upload_base_url(engine_id, upload_relay_base_urls())
    ttl = download_ticket_ttl()
    try:
        token = issue_download_ticket(
            engine_id=engine_id,
            torrent_id=body.torrent_id,
            path=path,
            uid=principal.name,
            ttl_seconds=ttl,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TicketOut(
        ticket=token,
        url=f"{direct}/download/v1/file",
        relay_url=f"{relay_base}/download/v1/file" if relay_base else None,
        filename=PurePosixPath(path).name,
        size=size,
        expires_in=ttl,
    )
