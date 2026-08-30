"""Проверка HMAC upload-ticket (контракт общий с api/seeding_api/upload_ticket.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketClaims:
    engine_id: str
    dest_dir: str
    filename: str
    size: int
    uid: str
    exp: int
    jti: str


class TicketError(ValueError):
    pass


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def verify_ticket(token: str, secret: str, *, now: float | None = None) -> TicketClaims:
    if not secret:
        raise TicketError("upload ticket secret not configured")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("malformed ticket")
    payload_b64, sig_b64 = parts
    payload = _b64url_decode(payload_b64)
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    try:
        got = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise TicketError("bad signature encoding") from exc
    if not hmac.compare_digest(expected, got):
        raise TicketError("invalid ticket signature")
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise TicketError("invalid ticket payload") from exc
    try:
        claims = TicketClaims(
            engine_id=str(data["eng"]),
            dest_dir=str(data["dir"]),
            filename=str(data["name"]),
            size=int(data["size"]),
            uid=str(data.get("uid", "")),
            exp=int(data["exp"]),
            jti=str(data["jti"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TicketError("ticket missing fields") from exc
    ts = time.time() if now is None else now
    if claims.exp < ts:
        raise TicketError("ticket expired")
    if not claims.engine_id or not claims.dest_dir or not claims.filename:
        raise TicketError("ticket empty path fields")
    if "/" in claims.filename or "\\" in claims.filename or claims.filename in (".", ".."):
        raise TicketError("invalid filename in ticket")
    if claims.size < 0:
        raise TicketError("invalid size")
    return claims


@dataclass(frozen=True)
class DownloadTicketClaims:
    engine_id: str
    torrent_id: int
    path: str
    uid: str
    exp: int
    jti: str


def normalize_download_path(path: str) -> str:
    """Относительный путь файла раздачи без '..' и абсолютного корня."""
    raw = (path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        raise TicketError("invalid download path")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise TicketError("invalid download path")
        parts.append(part)
    if not parts:
        raise TicketError("invalid download path")
    return "/".join(parts)


def verify_download_ticket(token: str, secret: str, *, now: float | None = None) -> DownloadTicketClaims:
    if not secret:
        raise TicketError("download ticket secret not configured")
    parts = token.split(".")
    if len(parts) != 2:
        raise TicketError("malformed ticket")
    payload_b64, sig_b64 = parts
    payload = _b64url_decode(payload_b64)
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    try:
        got = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise TicketError("bad signature encoding") from exc
    if not hmac.compare_digest(expected, got):
        raise TicketError("invalid ticket signature")
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise TicketError("invalid ticket payload") from exc
    if str(data.get("k", "")) != "dl":
        raise TicketError("not a download ticket")
    try:
        path = normalize_download_path(str(data["path"]))
        claims = DownloadTicketClaims(
            engine_id=str(data["eng"]),
            torrent_id=int(data["tid"]),
            path=path,
            uid=str(data.get("uid", "")),
            exp=int(data["exp"]),
            jti=str(data["jti"]),
        )
    except (KeyError, TypeError, ValueError, TicketError) as exc:
        raise TicketError("ticket missing fields") from exc
    ts = time.time() if now is None else now
    if claims.exp < ts:
        raise TicketError("ticket expired")
    if not claims.engine_id or claims.torrent_id < 1:
        raise TicketError("ticket empty identity")
    return claims


def issue_download_ticket(
    *,
    secret: str,
    engine_id: str,
    torrent_id: int,
    path: str,
    uid: str,
    ttl_seconds: int = 300,
    jti: str | None = None,
    now: float | None = None,
) -> str:
    """Только для тестов / зеркало API (прод выдаёт api)."""
    import secrets as _secrets

    ts = time.time() if now is None else now
    payload = {
        "k": "dl",
        "eng": engine_id,
        "tid": int(torrent_id),
        "path": normalize_download_path(path),
        "uid": uid,
        "exp": int(ts + ttl_seconds),
        "jti": jti or _secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url_encode(raw)}.{_b64url_encode(sig)}"


def issue_ticket(
    *,
    secret: str,
    engine_id: str,
    dest_dir: str,
    filename: str,
    size: int,
    uid: str,
    ttl_seconds: int = 3600,
    jti: str | None = None,
    now: float | None = None,
) -> str:
    """Только для тестов / зеркало API (прод выдаёт api)."""
    import secrets as _secrets

    ts = time.time() if now is None else now
    payload = {
        "eng": engine_id,
        "dir": dest_dir,
        "name": filename,
        "size": int(size),
        "uid": uid,
        "exp": int(ts + ttl_seconds),
        "jti": jti or _secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url_encode(raw)}.{_b64url_encode(sig)}"
