"""Публичные эндпоинты тестовой загрузки файлов (ticket + feature flag)."""

from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from seeding_api.auth import Principal, require_auth
from seeding_api.deps import DbSession, EnginePoolDep
from seeding_api.upload_limits import load_upload_limits
from seeding_api.upload_ticket import (
    issue_ticket,
    normalize_dest_dir,
    resolve_upload_base_url,
    ticket_secret,
    upload_base_urls,
    upload_enabled,
    upload_per_engine,
    upload_relay_base_urls,
    upload_relay_enabled,
)

router = APIRouter(tags=["upload"])

_FILENAME_RE = re.compile(r"^[^/\\]+$")


class FeaturesOut(BaseModel):
    enabled: bool
    relay_enabled: bool = False
    per_engine: bool = False
    max_parallel_uploads: int = 4
    chunk_concurrency: int = 4


class TicketIn(BaseModel):
    engine_id: str = Field(min_length=1, max_length=64)
    dest_dir: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0, le=50 * 1024 * 1024 * 1024)  # 50 GiB hard cap
    route: Literal["direct", "relay"] = "direct"


class TicketOut(BaseModel):
    ticket: str
    upload_base_url: str
    route: Literal["direct", "relay"] = "direct"
    engine_id: str
    dest_dir: str
    filename: str
    size: int
    expires_in: int


@router.get("/upload/features", response_model=FeaturesOut)
async def upload_features(session: DbSession, _: Principal = Depends(require_auth)):
    limits = await load_upload_limits(session)
    return FeaturesOut(
        enabled=upload_enabled() and bool(ticket_secret()) and bool(upload_base_urls()),
        relay_enabled=upload_relay_enabled(),
        per_engine=upload_per_engine(),
        max_parallel_uploads=limits["max_parallel_uploads"],
        chunk_concurrency=limits["chunk_concurrency"],
    )


@router.post("/upload/ticket", response_model=TicketOut)
async def create_upload_ticket(
    body: TicketIn,
    pool: EnginePoolDep,
    principal: Principal = Depends(require_auth),
):
    if not upload_enabled():
        raise HTTPException(status_code=503, detail="file upload is disabled")
    if not ticket_secret():
        raise HTTPException(status_code=503, detail="upload ticket secret not configured")
    if not _FILENAME_RE.match(body.filename) or body.filename in (".", ".."):
        raise HTTPException(status_code=422, detail="invalid filename")
    spec = pool.spec(body.engine_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown engine {body.engine_id}")
    try:
        dest_dir = normalize_dest_dir(spec.storage_prefix, body.dest_dir)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    route = body.route
    if route == "relay":
        bases = upload_relay_base_urls()
        if not bases:
            raise HTTPException(status_code=503, detail="upload relay is not configured")
    else:
        bases = upload_base_urls()
    base = resolve_upload_base_url(body.engine_id, bases)
    if not base:
        raise HTTPException(
            status_code=503,
            detail=f"no upload base URL for engine {body.engine_id} (route={route})",
        )
    ttl = int(os.getenv("SEEDING_UPLOAD_TICKET_TTL", "7200"))
    try:
        token = issue_ticket(
            engine_id=body.engine_id,
            dest_dir=dest_dir,
            filename=body.filename,
            size=body.size,
            uid=principal.name,
            ttl_seconds=ttl,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TicketOut(
        ticket=token,
        upload_base_url=base,
        route=route,
        engine_id=body.engine_id,
        dest_dir=dest_dir,
        filename=body.filename,
        size=body.size,
        expires_in=ttl,
    )
