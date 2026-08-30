"""Публичное скачивание файла раздачи: /download/v1 (HMAC ticket, не /internal/v1)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from seeding_upload.ticket import TicketError, verify_download_ticket

log = logging.getLogger(__name__)


def _engine_id() -> str:
    return (os.getenv("SEEDING_ENGINE_ID") or os.getenv("ENGINE_STORAGE_SUBDIR") or "").strip()


def _secret() -> str:
    return (
        os.getenv("SEEDING_DOWNLOAD_TICKET_SECRET", "").strip()
        or os.getenv("SEEDING_UPLOAD_TICKET_SECRET", "").strip()
    )


def _content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _parse_range(range_header: str | None, size: int) -> tuple[int, int, int]:
    start = 0
    end = size - 1
    status = 200
    if range_header and range_header.startswith("bytes="):
        spec = range_header[len("bytes=") :].split(",")[0].strip()
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
    return start, end, status


def _stream_file(fp: Path, start: int, length: int):
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

    return gen()


def build_download_router(
    secret_fn,
    *,
    expected_engine_id: str | None = None,
) -> APIRouter:
    router = APIRouter()

    def _claims(ticket: str | None, authorization: str | None, x_ticket: str | None):
        token = (ticket or "").strip()
        if not token and x_ticket:
            token = x_ticket.strip()
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="missing download ticket")
        try:
            claims = verify_download_ticket(token, secret_fn())
        except TicketError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if expected_engine_id and claims.engine_id != expected_engine_id:
            raise HTTPException(status_code=403, detail="ticket engine mismatch")
        return claims

    @router.api_route("/download/v1/file", methods=["GET", "HEAD"])
    async def download_file(
        request: Request,
        ticket: str | None = Query(None),
        path: str | None = Query(None),
        range: str | None = Header(default=None),
        authorization: str | None = Header(None),
        x_download_ticket: str | None = Header(None, alias="X-Download-Ticket"),
    ):
        claims = _claims(ticket, authorization, x_download_ticket)
        if path:
            from seeding_upload.ticket import normalize_download_path

            try:
                if normalize_download_path(path) != claims.path:
                    raise HTTPException(status_code=400, detail="path does not match ticket")
            except TicketError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        rt = getattr(request.app.state, "torrent_runtime", None)
        fn = getattr(rt, "content_file_path", None) if rt is not None else None
        if fn is None:
            raise HTTPException(status_code=503, detail="download runtime unavailable")
        try:
            fp = await fn(claims.torrent_id, claims.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if fp is None or not Path(fp).is_file():
            raise HTTPException(status_code=404, detail="content file not found")
        fp = Path(fp)
        size = fp.stat().st_size
        if size <= 0:
            raise HTTPException(status_code=404, detail="content file empty")
        start, end, status = _parse_range(range, size)
        length = max(0, end - start + 1)
        headers = {
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Content-Disposition": _content_disposition(Path(claims.path).name),
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD":
            return Response(
                status_code=status,
                headers=headers,
                media_type="application/octet-stream",
            )
        return StreamingResponse(
            _stream_file(fp, start, length),
            status_code=status,
            media_type="application/octet-stream",
            headers=headers,
        )

    return router


def mount_download(app) -> bool:
    eid = _engine_id()
    if not _secret() or not eid:
        log.info("download HTTP off (need ticket secret and SEEDING_ENGINE_ID)")
        return False
    app.include_router(build_download_router(_secret, expected_engine_id=eid))
    log.info("download HTTP on engine=%s", eid)
    return True
