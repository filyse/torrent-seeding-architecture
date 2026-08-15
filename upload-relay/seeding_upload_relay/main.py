"""Полуприёмник: принимает чанки и сразу проксирует на upload-хосты контуров a/b."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from seeding_upload_relay import __version__

log = logging.getLogger("upload-relay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HOP_BY_HOP = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    "content-encoding",
}


def _cors_origins() -> list[str]:
    raw = os.getenv("UPLOAD_CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [x.strip() for x in raw.split(",") if x.strip()]


def _upstreams() -> dict[str, str]:
    """Контур → базовый URL upload (как NPM /u/a|/u/b)."""
    raw = os.getenv("UPLOAD_RELAY_UPSTREAMS", "").strip()
    if not raw:
        # Прод-дефолт: публичный фронт seedbox2 (доступен с RU VDS).
        return {
            "a": "https://seedbox2.hw-s.ru/u/a",
            "b": "https://seedbox2.hw-s.ru/u/b",
        }
    data = json.loads(raw)
    if not isinstance(data, dict) or not data:
        raise RuntimeError("UPLOAD_RELAY_UPSTREAMS must be a non-empty JSON object")
    return {str(k).strip().lower(): str(v).rstrip("/") for k, v in data.items()}


UPSTREAMS = _upstreams()
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        follow_redirects=False,
        http2=False,
    )
    log.info("relay up version=%s contours=%s", __version__, sorted(UPSTREAMS))
    try:
        yield
    finally:
        await _client.aclose()
        _client = None


app = FastAPI(title="seeding-upload-relay", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _filter_req_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        out[key] = value
    return out


def _filter_resp_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HOP_BY_HOP or key.lower() == "content-encoding":
            continue
        out[key] = value
    return out


@app.get("/health")
async def health():
    return {
        "ok": True,
        "role": "relay",
        "version": __version__,
        "contours": sorted(UPSTREAMS.keys()),
    }


@app.api_route(
    "/u/{contour}/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy(contour: str, full_path: str, request: Request) -> Response:
    key = contour.strip().lower()
    base = UPSTREAMS.get(key)
    if not base:
        raise HTTPException(status_code=404, detail=f"unknown contour {contour}")
    url = f"{base}/{full_path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Чанки до ~8 МБ — держим в RAM и сразу шлём апстриму (без диска).
    body = await request.body()
    assert _client is not None
    try:
        upstream = await _client.request(
            request.method,
            url,
            headers=_filter_req_headers(request),
            content=body if body else None,
        )
    except httpx.RequestError as exc:
        log.warning("upstream %s %s failed: %s", request.method, url, exc)
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
