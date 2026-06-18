"""Per-torrent fastresume: пути и save/load для libtorrent."""

from __future__ import annotations

import logging
import os
import time
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


def resume_alert_mask(lt) -> int | None:
    """Маска категорий алертов, необходимых для save_resume_data (libtorrent 2.0)."""
    cat = getattr(lt, "alert_category", None)
    if cat is None:
        return None
    mask = 0
    for name in ("error", "storage"):
        v = getattr(cat, name, None)
        if v is not None:
            mask |= int(v)
    return mask or None


def _resume_save_flags(lt) -> int:
    flags = 0
    f = getattr(lt, "save_resume_flags_t", None)
    if f is not None:
        for name in ("flush_disk_cache", "save_info_dict"):
            v = getattr(f, name, None)
            if v is not None:
                flags |= int(v)
    return flags


def _ih_key(handle) -> str | None:
    """Стабильный ключ раздачи (info-hash) для сопоставления алерта с db_id."""
    try:
        ihs = handle.info_hashes()
        v1 = getattr(ihs, "v1", None)
        if v1 is not None and str(v1) != "0" * 40:
            return str(v1)
        v2 = getattr(ihs, "v2", None)
        if v2 is not None:
            return str(v2)
    except Exception:  # noqa: BLE001
        pass
    try:
        return str(handle.info_hash())
    except Exception:  # noqa: BLE001
        return None


def save_resume_data_blocking(lt, ses, handles: dict, timeout: float = 15.0) -> int:
    """Сохранить fastresume для набора раздач через async-механизм libtorrent 2.0.

    handle.save_resume_data() в 2.0 — асинхронный: данные приходят отдельным
    save_resume_data_alert с актуальными счётчиками (all_time_upload и т.д.).
    Без этого upload/download за всё время не переживают рестарт движка.

    handles: {db_id -> torrent_handle}. Возвращает число сохранённых раздач.
    """
    if not handles:
        return 0
    flags = _resume_save_flags(lt)
    pending: dict[str, int] = {}
    for db_id, h in handles.items():
        try:
            if hasattr(h, "is_valid") and not h.is_valid():
                continue
            key = _ih_key(h)
            if not key:
                continue
            try:
                h.save_resume_data(flags)
            except TypeError:
                h.save_resume_data()
            pending[key] = db_id
        except Exception as exc:  # noqa: BLE001
            log.warning("save_resume_data request db_id=%s failed: %s", db_id, exc)
    if not pending:
        return 0

    rda = getattr(lt, "save_resume_data_alert", None)
    rdfa = getattr(lt, "save_resume_data_failed_alert", None)
    saved = 0
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        try:
            ses.wait_for_alert(500)
        except Exception:  # noqa: BLE001
            pass
        for a in ses.pop_alerts():
            if rda is not None and isinstance(a, rda):
                key = _ih_key(a.handle)
                db_id = pending.pop(key, None) if key else None
                if db_id is None:
                    continue
                try:
                    blob = lt.write_resume_data_buf(a.params)
                    path = fastresume_path(db_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(blob)
                    saved += 1
                    log.debug("fastresume saved db_id=%s path=%s", db_id, path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("fastresume write db_id=%s failed: %s", db_id, exc)
            elif rdfa is not None and isinstance(a, rdfa):
                key = _ih_key(a.handle)
                db_id = pending.pop(key, None) if key else None
                log.warning(
                    "save_resume_data failed db_id=%s: %s",
                    db_id, getattr(a, "error", a),
                )
    if pending:
        log.warning("fastresume save timed out for db_ids=%s", list(pending.values()))
    return saved


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
