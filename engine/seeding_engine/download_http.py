"""Публичное скачивание файла раздачи: /download/v1 (HMAC ticket, не /internal/v1)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from seeding_upload.ticket import TicketError, verify_download_ticket, verify_zip_ticket

from seeding_engine.zipstream import ZipEntry, ZipPlan

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

    zip_active = {"n": 0}

    @router.api_route("/download/v1/zip", methods=["GET", "HEAD"])
    async def download_zip(
        request: Request,
        ticket: str | None = Query(None),
        name: str | None = Query(None, max_length=300),
        authorization: str | None = Header(None),
        x_download_ticket: str | None = Header(None, alias="X-Download-Ticket"),
    ):
        claims = _zip_claims(ticket, authorization, x_download_ticket)
        rt = getattr(request.app.state, "torrent_runtime", None)
        list_fn = getattr(rt, "list_files", None) if rt is not None else None
        path_fn = getattr(rt, "content_file_path", None) if rt is not None else None
        if list_fn is None or path_fn is None:
            raise HTTPException(status_code=503, detail="download runtime unavailable")
        entries = await _zip_entries(claims.torrent_id, list_fn, path_fn)
        if not entries:
            raise HTTPException(status_code=404, detail="no complete files to zip")
        plan = ZipPlan(entries)
        headers = {
            "Content-Length": str(plan.total),
            "Accept-Ranges": "none",
            "Content-Disposition": _content_disposition(_zip_name(name, claims.torrent_id)),
            "Cache-Control": "no-store",
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type="application/zip")
        # Не больше N ZIP-потоков на движок одновременно: чтение целиком с диска
        # конкурирует с раздачей. Счётчик растёт только когда поток реально пошёл.
        if zip_active["n"] >= _zip_max_parallel():
            raise HTTPException(status_code=429, detail="too many zip downloads, try later")

        tid = claims.torrent_id

        async def body():
            try:
                async for part in plan.stream(_read_entry):
                    yield part
            except OSError as exc:
                log.warning("zip download aborted tid=%s: %s", tid, exc)
                raise

        return _CountedStream(zip_active, tid, body(), status_code=200, media_type="application/zip", headers=headers)

    def _zip_claims(ticket, authorization, x_ticket):
        token = (ticket or "").strip() or (x_ticket or "").strip()
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="missing download ticket")
        try:
            claims = verify_zip_ticket(token, secret_fn())
        except TicketError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if expected_engine_id and claims.engine_id != expected_engine_id:
            raise HTTPException(status_code=403, detail="ticket engine mismatch")
        return claims

    return router


class _CountedStream(StreamingResponse):
    """Счётчик активных ZIP-потоков: +1 на старте отдачи, −1 в finally вокруг всей
    отдачи. Генератор при обрыве клиента Starlette не закрывает явно, поэтому
    считаем здесь, а не в finally генератора."""

    def __init__(self, counter: dict, tid: int, content, **kw):
        super().__init__(content, **kw)
        self._counter = counter
        self._tid = tid

    async def __call__(self, scope, receive, send):
        self._counter["n"] += 1
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._counter["n"] -= 1
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass
            log.info("zip stream closed tid=%s active=%s", self._tid, self._counter["n"])


def _zip_max_parallel() -> int:
    try:
        return max(1, int(os.getenv("SEEDING_DOWNLOAD_ZIP_PARALLEL", "2")))
    except ValueError:
        return 2


_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _zip_name(name: str | None, torrent_id: int) -> str:
    base = _BAD_NAME.sub("_", (name or "").strip()).strip(" .")[:200]
    if base.lower().endswith(".zip"):
        base = base[:-4]
    return f"{base or f'torrent-{torrent_id}'}.zip"


async def _zip_entries(torrent_id: int, list_fn, path_fn) -> list[ZipEntry]:
    """Только готовые файлы (progress 100%, размер на диске = размер в раздаче)."""
    files = await list_fn(torrent_id)
    out: list[ZipEntry] = []
    seen: set[str] = set()
    for f in files:
        rel = str(f.get("path") or "").replace("\\", "/")
        size = int(f.get("size") or 0)
        if not rel or int(f.get("downloaded") or 0) < size:
            continue
        try:
            fp = await path_fn(torrent_id, rel)
        except ValueError:
            continue
        if fp is None:
            continue
        fp = Path(fp)
        try:
            st = fp.stat()
        except OSError:
            continue
        if not fp.is_file() or st.st_size != size:
            continue
        name = "/".join(p for p in rel.split("/") if p not in ("", ".", ".."))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(ZipEntry(name=name, size=size, mtime=st.st_mtime, path=fp))
    return out


async def _read_entry(e: ZipEntry):
    loop = asyncio.get_running_loop()
    f = await loop.run_in_executor(None, open, e.path, "rb")
    try:
        remaining = e.size
        while remaining > 0:
            chunk = await loop.run_in_executor(None, f.read, min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        f.close()


def mount_download(app) -> bool:
    eid = _engine_id()
    if not _secret() or not eid:
        log.info("download HTTP off (need ticket secret and SEEDING_ENGINE_ID)")
        return False
    app.include_router(build_download_router(_secret, expected_engine_id=eid))
    log.info("download HTTP on engine=%s", eid)
    return True
