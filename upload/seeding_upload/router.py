"""HTTP-роутер чанковой загрузки. Один и тот же в sidecar и в движке."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from seeding_upload import __version__
from seeding_upload.storage import StorageError, UploadStorage
from seeding_upload.ticket import TicketError, verify_ticket


class CreateIn(BaseModel):
    overwrite: bool = False


class CreateOut(BaseModel):
    id: str
    chunk_size: int
    chunk_count: int
    size: int
    received: list[int]
    dest_dir: str
    filename: str


class StatusOut(BaseModel):
    id: str
    chunk_size: int
    chunk_count: int
    size: int
    received: list[int]
    cancel_at: float | None = None
    dest_dir: str
    filename: str


class CompleteOut(BaseModel):
    path: str
    size: int


def build_upload_router(
    storage: UploadStorage,
    secret_fn: Callable[[], str],
    *,
    expected_engine_id: str | None = None,
) -> APIRouter:
    """expected_engine_id — если задан, ticket.eng должен совпасть (режим движка)."""

    router = APIRouter()

    def _bearer(authorization: str | None, x_upload_ticket: str | None) -> str:
        if x_upload_ticket and x_upload_ticket.strip():
            return x_upload_ticket.strip()
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        raise HTTPException(status_code=401, detail="missing upload ticket")

    def _claims(authorization: str | None, x_upload_ticket: str | None):
        token = _bearer(authorization, x_upload_ticket)
        try:
            claims = verify_ticket(token, secret_fn())
        except TicketError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if expected_engine_id and claims.engine_id != expected_engine_id:
            raise HTTPException(status_code=403, detail="ticket engine mismatch")
        return claims

    @router.post("/upload/v1/uploads", response_model=CreateOut)
    async def create_upload(
        body: CreateIn,
        authorization: str | None = Header(None),
        x_upload_ticket: str | None = Header(None, alias="X-Upload-Ticket"),
    ):
        claims = _claims(authorization, x_upload_ticket)
        existing = storage.find_by_jti(claims.engine_id, claims.jti)
        if existing is not None:
            if existing.size != claims.size or existing.filename != claims.filename:
                raise HTTPException(status_code=409, detail="ticket jti reused with different file")
            return CreateOut(
                id=existing.id,
                chunk_size=existing.chunk_size,
                chunk_count=existing.chunk_count,
                size=existing.size,
                received=storage.received_chunks(existing),
                dest_dir=existing.dest_dir,
                filename=existing.filename,
            )
        upload_id = secrets.token_urlsafe(12)
        try:
            sess = storage.create(
                upload_id=upload_id,
                engine_id=claims.engine_id,
                dest_dir=claims.dest_dir,
                filename=claims.filename,
                size=claims.size,
                jti=claims.jti,
                overwrite=body.overwrite,
            )
        except StorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CreateOut(
            id=sess.id,
            chunk_size=sess.chunk_size,
            chunk_count=sess.chunk_count,
            size=sess.size,
            received=[],
            dest_dir=sess.dest_dir,
            filename=sess.filename,
        )

    @router.get("/upload/v1/uploads/{upload_id}", response_model=StatusOut)
    async def upload_status(
        upload_id: str,
        authorization: str | None = Header(None),
        x_upload_ticket: str | None = Header(None, alias="X-Upload-Ticket"),
    ):
        claims = _claims(authorization, x_upload_ticket)
        try:
            sess = storage.load(claims.engine_id, upload_id)
        except StorageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if sess.jti != claims.jti:
            raise HTTPException(status_code=403, detail="ticket does not match upload")
        return StatusOut(
            id=sess.id,
            chunk_size=sess.chunk_size,
            chunk_count=sess.chunk_count,
            size=sess.size,
            received=storage.received_chunks(sess),
            cancel_at=sess.cancel_at,
            dest_dir=sess.dest_dir,
            filename=sess.filename,
        )

    @router.put("/upload/v1/uploads/{upload_id}/chunks/{index}")
    async def put_chunk(
        upload_id: str,
        index: int,
        request: Request,
        authorization: str | None = Header(None),
        x_upload_ticket: str | None = Header(None, alias="X-Upload-Ticket"),
    ):
        claims = _claims(authorization, x_upload_ticket)
        try:
            sess = storage.load(claims.engine_id, upload_id)
        except StorageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if sess.jti != claims.jti:
            raise HTTPException(status_code=403, detail="ticket does not match upload")
        data = await request.body()
        try:
            storage.write_chunk(sess, index, data)
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "index": index, "received": storage.received_chunks(sess)}

    @router.post("/upload/v1/uploads/{upload_id}/complete", response_model=CompleteOut)
    async def complete_upload(
        upload_id: str,
        authorization: str | None = Header(None),
        x_upload_ticket: str | None = Header(None, alias="X-Upload-Ticket"),
    ):
        claims = _claims(authorization, x_upload_ticket)
        try:
            sess = storage.load(claims.engine_id, upload_id)
        except StorageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if sess.jti != claims.jti:
            raise HTTPException(status_code=403, detail="ticket does not match upload")
        try:
            path = storage.complete(sess)
        except StorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CompleteOut(path=str(path), size=sess.size)

    @router.delete("/upload/v1/uploads/{upload_id}")
    async def cancel_upload(
        upload_id: str,
        authorization: str | None = Header(None),
        x_upload_ticket: str | None = Header(None, alias="X-Upload-Ticket"),
    ):
        claims = _claims(authorization, x_upload_ticket)
        try:
            sess = storage.load(claims.engine_id, upload_id)
        except StorageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if sess.jti != claims.jti:
            raise HTTPException(status_code=403, detail="ticket does not match upload")
        storage.schedule_cancel(sess)
        return Response(status_code=204)

    @router.get("/upload/v1/config")
    async def public_config():
        return {
            "chunk_size": storage.chunk_size,
            "engines": sorted(storage.roots.keys()),
            "version": __version__,
            "embedded": bool(expected_engine_id),
        }

    return router
