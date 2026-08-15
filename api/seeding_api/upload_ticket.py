"""Выдача HMAC upload-ticket (контракт общий с upload/seeding_upload/ticket.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def ticket_secret() -> str:
    return os.getenv("SEEDING_UPLOAD_TICKET_SECRET", "").strip()


def upload_enabled() -> bool:
    return os.getenv("SEEDING_UPLOAD_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def upload_base_urls() -> dict[str, str]:
    raw = os.getenv("SEEDING_UPLOAD_BASE_URLS", "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("SEEDING_UPLOAD_BASE_URLS must be a JSON object")
    return {str(k): str(v).rstrip("/") for k, v in data.items()}


def upload_relay_base_urls() -> dict[str, str]:
    """Базы через RU-релей (тот же shape, что SEEDING_UPLOAD_BASE_URLS)."""
    raw = os.getenv("SEEDING_UPLOAD_RELAY_BASE_URLS", "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("SEEDING_UPLOAD_RELAY_BASE_URLS must be a JSON object")
    return {str(k): str(v).rstrip("/") for k, v in data.items()}


def upload_relay_enabled() -> bool:
    return bool(upload_relay_base_urls())


def upload_per_engine() -> bool:
    """URL вида /u/b/{engine_id} → edge nginx → этот движок. Sidecar без суффикса — выкл."""
    return os.getenv("SEEDING_UPLOAD_PER_ENGINE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def resolve_upload_base_url(engine_id: str, bases: dict[str, str]) -> str | None:
    """База для браузера. Подставляет {engine_id}/{contour}; в per-engine дописывает /id."""
    raw = bases.get(engine_id) or bases.get("*")
    if not raw:
        return None
    contour = engine_id[0] if engine_id[:1].isalpha() else ""
    raw = raw.replace("{engine_id}", engine_id).replace("{contour}", contour).rstrip("/")
    if upload_per_engine() and not raw.endswith("/" + engine_id):
        raw = f"{raw}/{engine_id}"
    return raw


def issue_ticket(
    *,
    engine_id: str,
    dest_dir: str,
    filename: str,
    size: int,
    uid: str,
    ttl_seconds: int | None = None,
) -> str:
    secret = ticket_secret()
    if not secret:
        raise RuntimeError("SEEDING_UPLOAD_TICKET_SECRET is not set")
    if ttl_seconds is None:
        ttl_seconds = int(os.getenv("SEEDING_UPLOAD_TICKET_TTL", "7200"))
    payload = {
        "eng": engine_id,
        "dir": dest_dir,
        "name": filename,
        "size": int(size),
        "uid": uid,
        "exp": int(time.time() + ttl_seconds),
        "jti": secrets.token_urlsafe(16),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url_encode(raw)}.{_b64url_encode(sig)}"


def normalize_dest_dir(storage_prefix: str, dest_dir: str) -> str:
    """Нормализовать и проверить, что dest_dir внутри storage_prefix движка."""
    prefix = os.path.normpath(storage_prefix)
    dest = os.path.normpath(dest_dir)
    if not os.path.isabs(dest):
        raise ValueError("dest_dir must be absolute")
    if dest != prefix and not dest.startswith(prefix + os.sep):
        # Также допускаем POSIX-слеши в конфиге на Windows-агенте.
        prefix_p = prefix.replace("\\", "/")
        dest_p = dest.replace("\\", "/")
        if dest_p != prefix_p and not dest_p.startswith(prefix_p + "/"):
            raise ValueError("dest_dir outside engine storage_prefix")
    if "/.upload-tmp" in dest.replace("\\", "/") or dest.replace("\\", "/").endswith(
        "/.upload-tmp"
    ):
        raise ValueError("dest_dir must not be .upload-tmp")
    return dest.replace("\\", "/")
