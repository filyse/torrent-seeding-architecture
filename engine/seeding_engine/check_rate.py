"""Скорость проверки одной раздачи.

libtorrent не отдаёт байт/с на хеш. Он шлёт piece_finished_alert, когда кусок
прошёл проверку. Складываем размеры этих кусков за окно и делим на окно.
Куски из fast resume в это окно не попадают: их учитываем только пока
раздача в checking_files.
"""

from __future__ import annotations


class CheckRate:
    def __init__(self, window_s: float = 8.0) -> None:
        self._window = max(1.0, float(window_s))
        self._events: dict[int, list[tuple[float, int]]] = {}

    def add(self, db_id: int, nbytes: int, now: float) -> None:
        if nbytes <= 0:
            return
        bucket = self._events.setdefault(int(db_id), [])
        bucket.append((float(now), int(nbytes)))
        self._prune(int(db_id), float(now))

    def rate(self, db_id: int, now: float) -> int:
        """Байт/с за окно. Нет кусков в окне — 0."""
        key = int(db_id)
        self._prune(key, float(now))
        bucket = self._events.get(key) or []
        if not bucket:
            return 0
        total = sum(n for _, n in bucket)
        return int(total / self._window)

    def drop(self, db_id: int) -> None:
        self._events.pop(int(db_id), None)

    def _prune(self, db_id: int, now: float) -> None:
        bucket = self._events.get(db_id)
        if not bucket:
            return
        cutoff = now - self._window
        kept = [item for item in bucket if item[0] >= cutoff]
        if kept:
            self._events[db_id] = kept
        else:
            self._events.pop(db_id, None)
