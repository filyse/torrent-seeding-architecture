"""Полуприёмник: принимает чанки и сразу проксирует на upload-хосты контуров a/b."""

from __future__ import annotations

import asyncio
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

# Сколько раз повторить PUT/POST на seedbox, если канал оборвался.
_UPSTREAM_RETRIES = 3


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


def format_upstream_error(exc: BaseException) -> str:
    """Текст для 502: httpx часто даёт пустой str(exc) на RST/EOF."""
    msg = str(exc).strip()
    cause = exc.__cause__ or exc.__context__
    cause_s = ""
    if cause is not None and cause is not exc:
        cause_s = str(cause).strip() or type(cause).__name__
    bits = [type(exc).__name__]
    if msg:
        bits.append(msg)
    if cause_s and cause_s != msg:
        bits.append(cause_s)
    if len(bits) == 1:
        bits.append("connection dropped")
    return ": ".join(bits)


UPSTREAMS = _upstreams()
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _client
    # Keep-alive на пути RU→дом часто отдаёт полузакрытый сокет: следующий чанк
    # падает с пустым RequestError. Новое соединение на каждый запрос надёжнее.
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        follow_redirects=False,
        http2=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=16),
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
    headers = _filter_req_headers(request)
    last_exc: httpx.RequestError | None = None
    for attempt in range(1, _UPSTREAM_RETRIES + 1):
        try:
            upstream = await _client.request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
            )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=_filter_resp_headers(upstream.headers),
                media_type=upstream.headers.get("content-type"),
            )
        except httpx.RequestError as exc:
            last_exc = exc
            detail = format_upstream_error(exc)
            log.warning(
                "upstream %s %s failed attempt=%s/%s: %s",
                request.method,
                url,
                attempt,
                _UPSTREAM_RETRIES,
                detail,
            )
            if attempt < _UPSTREAM_RETRIES:
                await asyncio.sleep(0.4 * attempt)

    assert last_exc is not None
    raise HTTPException(
        status_code=502, detail=f"upstream error: {format_upstream_error(last_exc)}"
    ) from last_exc
