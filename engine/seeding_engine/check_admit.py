"""Кто на движке имеет право читать файлы для проверки.

После рестарта libtorrent с auto_managed=False проверяет все раздачи сразу,
и active_checking его не останавливает. Здесь раздачи сначала только
классифицируются (быстрый resume), а полный проход по файлам идёт не больше
чем в N слотов — тот же лимит, что у кнопки «Перепроверить».

Пока слота нет, раздача стоит на паузе libtorrent. Это не пауза пользователя:
в снимок она уходит как queued_for_checking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from seeding_engine.upload_hold import is_full_hash_check_state

# Состояния, в которых слот ещё нельзя отпустить: проверка не началась или это не она.
_TRANSIENT = frozenset({"", "checking_resume_data", "allocating"})
# Проверка не нужна — раздача уже сидирует или качает.
_SETTLED = frozenset({"seeding", "finished", "downloading", "downloading_metadata"})


@dataclass(frozen=True)
class AdmitActions:
    resume: list[int]
    pause: list[int]


class CheckAdmit:
    def __init__(self, limit: int = 2, *, probe_timeout: float = 8.0) -> None:
        self._limit = max(1, int(limit))
        self._probe_timeout = float(probe_timeout)
        self._held: list[int] = []
        self._queued: list[int] = []
        self._probing: dict[int, float] = {}
        self._hashing: dict[int, float] = {}
        self._waiting: set[int] = set()
        self._known: set[int] = set()
        self._saw_full: set[int] = set()

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> int:
        self._limit = max(1, int(limit))
        return self._limit

    def hold(self, db_id: int) -> None:
        """Раздача добавлена на паузе и ждёт классификации."""
        if db_id in self._known:
            return
        self._known.add(db_id)
        self._held.append(db_id)
        self._waiting.add(db_id)

    def drop(self, db_id: int) -> None:
        """Убрать из очереди: пауза пользователя, удаление или явный recheck."""
        self._known.discard(db_id)
        self._waiting.discard(db_id)
        self._saw_full.discard(db_id)
        self._probing.pop(db_id, None)
        self._hashing.pop(db_id, None)
        self._held = [i for i in self._held if i != db_id]
        self._queued = [i for i in self._queued if i != db_id]

    def is_waiting(self, db_id: int) -> bool:
        """На паузе очереди, ещё не читает диск. В UI это не «Пауза»."""
        return db_id in self._waiting

    def watch_ids(self) -> list[int]:
        return list(self._probing) + list(self._hashing)

    def counts(self) -> dict[str, int]:
        return {
            "held": len(self._held),
            "queued": len(self._queued),
            "probing": len(self._probing),
            "hashing": len(self._hashing),
        }

    def tick(
        self,
        states: dict[int, str | None],
        *,
        busy: int = 0,
        now: float,
    ) -> AdmitActions:
        """busy — слоты, которые уже занял явный recheck (HashQueue)."""
        resume: list[int] = []
        pause: list[int] = []
        self._settle_probes(states, now, pause)
        self._settle_hashes(states, now)
        self._shrink(max(0, int(busy)), pause)

        slots = self._limit - len(self._probing) - len(self._hashing) - max(0, int(busy))
        if self._held:
            # Сначала понять, кому вообще нужен полный проход. Иначе длинный хеш
            # держит слот, и готовые сиды за ним не встают.
            while slots > 0 and self._held:
                db_id = self._held.pop(0)
                self._waiting.discard(db_id)
                self._probing[db_id] = now
                resume.append(db_id)
                slots -= 1
        else:
            while slots > 0 and self._queued:
                db_id = self._queued.pop(0)
                self._waiting.discard(db_id)
                self._hashing[db_id] = now
                resume.append(db_id)
                slots -= 1
        # Уже идущую проверку не ставим на паузу только затем, чтобы сразу снять её:
        # слот свободен, раздача и так resume.
        stayed = {i for i in resume if i in pause}
        if stayed:
            resume = [i for i in resume if i not in stayed]
            pause = [i for i in pause if i not in stayed]
        return AdmitActions(resume, pause)

    def _settle_probes(
        self,
        states: dict[int, str | None],
        now: float,
        pause: list[int],
    ) -> None:
        for db_id, started in list(self._probing.items()):
            if db_id not in states:
                continue
            st = states[db_id]
            if st is None:
                self.drop(db_id)
                continue
            label = st.strip().lower()
            if is_full_hash_check_state(label):
                self._probing.pop(db_id, None)
                self._queued.append(db_id)
                self._waiting.add(db_id)
                pause.append(db_id)
                continue
            if label in _SETTLED or (
                label not in _TRANSIENT and now - started >= self._probe_timeout
            ):
                self._release(db_id, self._probing)
                continue
            if now - started >= self._probe_timeout:
                self._release(db_id, self._probing)

    def _settle_hashes(self, states: dict[int, str | None], now: float) -> None:
        for db_id, started in list(self._hashing.items()):
            if db_id not in states:
                continue
            st = states[db_id]
            if st is None:
                self.drop(db_id)
                continue
            label = st.strip().lower()
            if is_full_hash_check_state(label):
                self._saw_full.add(db_id)
                continue
            if label in _TRANSIENT:
                if db_id not in self._saw_full and now - started >= self._probe_timeout:
                    self._release(db_id, self._hashing)
                continue
            self._release(db_id, self._hashing)

    def _shrink(self, busy: int, pause: list[int]) -> None:
        overflow = len(self._probing) + len(self._hashing) + busy - self._limit
        for db_id in list(self._probing):
            if overflow <= 0:
                return
            self._probing.pop(db_id, None)
            self._held.insert(0, db_id)
            self._waiting.add(db_id)
            pause.append(db_id)
            overflow -= 1

    def _release(self, db_id: int, pool: dict[int, float]) -> None:
        pool.pop(db_id, None)
        self._known.discard(db_id)
        self._waiting.discard(db_id)
        self._saw_full.discard(db_id)


class UserPauseStore:
    """Пауза, которую поставил человек. Очередь проверки паузит раздачи тоже,
    и в fastresume они выглядят так же. Без этого файла после рестарта
    очередь приняла бы ручную паузу за свою и сняла её."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ids: set[int] = set()
        self.ready = path.is_file()
        if self.ready:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.ids = {int(x) for x in raw} if isinstance(raw, list) else set()
            except (OSError, ValueError, TypeError):
                self.ids = set()

    def note_legacy(self, db_id: int) -> None:
        self.ids.add(int(db_id))

    def is_user(self, db_id: int) -> bool:
        return int(db_id) in self.ids

    def mark(self, db_id: int) -> None:
        self.ids.add(int(db_id))
        self._write()

    def clear(self, db_id: int) -> None:
        self.ids.discard(int(db_id))
        self._write()

    def seal(self) -> None:
        """Зафиксировать файл, даже пустой: следующий старт уже отличает
        паузу очереди от паузы пользователя."""
        self._write()
        self.ready = True

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.ids)), encoding="utf-8")
        tmp.replace(self.path)
