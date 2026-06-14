"""Per-torrent fastresume: пути и save/load для libtorrent."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def fastresume_dir() -> Path:
    custom = os.getenv("SEEDING_FASTRESUME_DIR", "").strip()
    if custom:
        return Path(custom)
    root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
    return root / ".fastresume"


def fastresume_path(db_id: int) -> Path:
    return fastresume_dir() / f"{db_id}.fastresume"


def session_state_path() -> Path | None:
    raw = os.getenv("SEEDING_LT_STATE_FILE", "").strip()
    return Path(raw) if raw else None


def ensure_engine_dirs(storage_subdir: str = "") -> None:
    """Создать каталоги данных на томе движка (entrypoint / старт)."""
    root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
    for rel in (".state", ".fastresume", ".torrents"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    sub = (storage_subdir or os.getenv("ENGINE_STORAGE_SUBDIR", "")).strip()
    if sub:
        (root / sub).mkdir(parents=True, exist_ok=True)


def save_fastresume(lt, handle, db_id: int) -> bool:
    path = fastresume_path(db_id)
    try:
        rd = handle.save_resume_data()
        if rd is None:
            return False
        blob = rd if isinstance(rd, bytes) else lt.bencode(rd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        log.debug("fastresume saved db_id=%s path=%s", db_id, path)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("fastresume save db_id=%s failed: %s", db_id, exc)
        return False


def delete_fastresume(db_id: int) -> None:
    path = fastresume_path(db_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            log.warning("fastresume delete db_id=%s: %s", db_id, exc)


def try_read_resume_params(lt, blob: bytes, save_path: str):
    """Вернуть add_torrent_params из fastresume или None."""
    try:
        params = lt.read_resume_data(blob)
    except Exception as exc:  # noqa: BLE001
        log.warning("read_resume_data failed: %s", exc)
        return None
    params.save_path = save_path
    flags = getattr(lt, "torrent_flags", None)
    if flags is not None and hasattr(flags, "auto_managed"):
        try:
            params.flags &= ~flags.auto_managed
        except Exception:  # noqa: BLE001
            pass
    return params
