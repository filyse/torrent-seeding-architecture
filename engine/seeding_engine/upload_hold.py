"""Временный потолок отдачи на время хеша .torrent и recheck на HDD.

Постоянный лимит сессии (БД / UI / heartbeat) не перезаписываем: hold живёт
в памяти процесса. SSD не трогаем — там хеш и отдача не дерутся за шпиндель.
"""

from __future__ import annotations

import os
import threading
import time

# Полный проход по файлам. checking_resume_data — только метаданные, не режем.
FULL_HASH_CHECK_STATES = frozenset(
    {"checking", "checking_files", "queued_for_checking"}
)
DEFAULT_CHECK_PENDING_GRACE_S = 8.0

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


def is_full_hash_check_state(state: str) -> bool:
    """True только для полного чтения файлов, не для checking_resume_data."""
    return str(state or "").strip().lower() in FULL_HASH_CHECK_STATES


class CheckHoldTracker:
    """Держит ref SessionUploadGate, пока на движке идёт полная проверка хеша.

    force_recheck возвращается сразу: note_recheck держит кап grace секунд,
    пока libtorrent не войдёт в checking_*. sync — источник правды по id.
    """

    def __init__(self, pending_grace_s: float = DEFAULT_CHECK_PENDING_GRACE_S) -> None:
        self._grace = max(0.0, float(pending_grace_s))
        self._held: set[int] = set()
        self._pending_until: dict[int, float] = {}
        self._last_checking: set[int] = set()
        self._lock = threading.Lock()

    def note_recheck(self, gate: SessionUploadGate, disk_kind: str, db_id: int) -> None:
        db_id = int(db_id)
        with self._lock:
            self._pending_until[db_id] = time.monotonic() + self._grace
            checking = set(self._last_checking)
        # Не кладём id в checking: иначе sync снимет pending в ту же миллисекунду.
        self.sync(gate, disk_kind, checking)

    def observe(
        self, gate: SessionUploadGate, disk_kind: str, db_id: int, state: str
    ) -> None:
        db_id = int(db_id)
        with self._lock:
            checking = set(self._last_checking)
        if is_full_hash_check_state(state):
            checking.add(db_id)
        else:
            checking.discard(db_id)
        self.sync(gate, disk_kind, checking)

    def sync(
        self, gate: SessionUploadGate, disk_kind: str, checking_ids: set[int]
    ) -> None:
        now = time.monotonic()
        checking_ids = {int(i) for i in checking_ids}
        with self._lock:
            for db_id, until in list(self._pending_until.items()):
                if db_id in checking_ids or now >= until:
                    self._pending_until.pop(db_id, None)
            wanted = checking_ids | set(self._pending_until)
            self._last_checking = set(checking_ids)
            added = wanted - self._held
            removed = self._held - wanted
            for db_id in added:
                if gate.begin_create(disk_kind):
                    self._held.add(db_id)
            for db_id in removed:
                if db_id in self._held:
                    self._held.discard(db_id)
                    gate.end_create()
