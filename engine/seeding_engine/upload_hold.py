"""Временный потолок отдачи на время хеша .torrent на HDD.

Постоянный лимит сессии (БД / UI / heartbeat) не перезаписываем: hold живёт
в памяти процесса. SSD не трогаем — там хеш и отдача не дерутся за шпиндель.
"""

from __future__ import annotations

import os
import threading

DEFAULT_CREATOR_UPLOAD_LIMIT_BPS = 1024 * 1024


def creator_upload_cap_bps() -> int:
    raw = os.getenv("SEEDING_CREATOR_UPLOAD_LIMIT_BPS", "").strip()
    if raw == "":
        return DEFAULT_CREATOR_UPLOAD_LIMIT_BPS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_CREATOR_UPLOAD_LIMIT_BPS


def should_hold_for_disk(kind: str, cap_bps: int | None = None) -> bool:
    """Hold только не-SSD. unknown считаем HDD: лучше прижать, чем ползти часами."""
    cap = creator_upload_cap_bps() if cap_bps is None else int(cap_bps)
    if cap <= 0:
        return False
    return str(kind or "unknown").strip().lower() != "ssd"


class UploadHold:
    def __init__(self, cap_bps: int | None = None) -> None:
        self.cap_bps = (
            creator_upload_cap_bps() if cap_bps is None else max(0, int(cap_bps))
        )
        self._refs = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._refs > 0 and self.cap_bps > 0

    def acquire(self) -> None:
        with self._lock:
            if self.cap_bps > 0:
                self._refs += 1

    def release(self) -> None:
        with self._lock:
            if self._refs > 0:
                self._refs -= 1

    def session_limit(self, desired: int) -> int:
        """Что поставить на сессию: hold режет только вверх от капа."""
        wanted = max(0, int(desired or 0))
        if not self.active:
            return wanted
        if wanted <= 0:
            return self.cap_bps
        return min(wanted, self.cap_bps)

    def snapshot(self) -> dict[str, object]:
        return {
            "creator_upload_hold": self.active,
            "creator_upload_hold_bps": self.cap_bps if self.active else 0,
        }


class SessionUploadGate:
    """Желаемый лимит сессии + hold на время хеша. apply(bps) пишет в libtorrent/mock."""

    def __init__(self, apply=None, desired: int = 0, cap_bps: int | None = None) -> None:
        self.desired = max(0, int(desired))
        self.hold = UploadHold(cap_bps)
        self._apply = apply
        self._lock = threading.Lock()

    def _push(self) -> int:
        applied = self.hold.session_limit(self.desired)
        if self._apply is not None:
            self._apply(applied)
        return applied

    def set_desired(self, bps: int) -> int:
        with self._lock:
            self.desired = max(0, int(bps))
            return self._push()

    def begin_create(self, disk_kind: str) -> bool:
        if not should_hold_for_disk(disk_kind, self.hold.cap_bps):
            return False
        with self._lock:
            self.hold.acquire()
            self._push()
            return True

    def end_create(self) -> None:
        with self._lock:
            was = self.hold.active
            self.hold.release()
            if was or self.hold.active:
                self._push()

    def stats(self) -> dict[str, object]:
        with self._lock:
            body = self.hold.snapshot()
            body["upload_limit_desired"] = self.desired
            return body
