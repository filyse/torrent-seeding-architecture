"""Создание .torrent-файлов из контента на диске движка.

Логика портирована из внешнего torrent_api (libtorrent 1.2) и адаптирована под
libtorrent 2.0 (v1-only). Работает поверх SEEDING_DATA_ROOT — того же тома, что
раздаёт движок, поэтому созданный торрент можно сразу поставить на сидинг без
копирования файлов.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)

PIECE_SIZE = 16 * 1024 * 1024  # 16 МБ, как в оригинальном torrent_api

# Хеширование выносим в ОТДЕЛЬНЫЙ процесс (start method "spawn"), чтобы CPU/IO-bound
# работа libtorrent не держала GIL и не блокировала событийный цикл движка. spawn
# (а не fork) — безопасно форкать многопоточный процесс движка нельзя.
_MP_CTX = multiprocessing.get_context("spawn")


def _default_workers() -> int:
    """Сколько задач создания хешировать одновременно на движке.

    На HDD параллельное хеширование двух папок вызывает seek-thrashing и замедляет
    обе — поэтому по умолчанию 1 (последовательно). Переопределяется env
    ``SEEDING_CREATOR_WORKERS`` (например, 2+ на SSD/NVMe-движках)."""
    try:
        return max(1, int(os.getenv("SEEDING_CREATOR_WORKERS", "1")))
    except ValueError:
        return 1


def _task_ttl() -> int:
    """Сколько секунд задача создания живёт в памяти движка, после чего автоудаляется.

    По умолчанию 24 часа. Переопределяется env ``SEEDING_CREATOR_TASK_TTL`` (секунды)."""
    try:
        return max(60, int(os.getenv("SEEDING_CREATOR_TASK_TTL", str(24 * 3600))))
    except ValueError:
        return 24 * 3600

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".ts",
    ".m2ts",
    ".wmv",
    ".flv",
    ".webm",
    ".mov",
}

EPISODE_PATTERNS = [
    re.compile(r"[Ss](\d+)[Ee](\d+)"),  # S01E05
    re.compile(r"[\[\(](\d+)[\]\)]"),  # [05] или (05)
    re.compile(r"(?:^|[_\s.\-])(\d+)(?:[_\s.\-]|$)"),  # _05_ .05. -05-
]


def _try_import_libtorrent():
    try:
        import libtorrent as lt  # type: ignore

        return lt
    except ImportError:
        return None


def data_root() -> str:
    return os.getenv("SEEDING_DATA_ROOT", "/data")


def _sanitize(rel_path: str) -> str:
    """Абсолютный путь внутри SEEDING_DATA_ROOT (защита от path traversal)."""
    root = os.path.abspath(data_root())
    rel = (rel_path or "").lstrip("/")
    full = os.path.abspath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path traversal detected")
    return full


def browse(path: str = "") -> list[dict[str, object]]:
    """Листинг директории относительно SEEDING_DATA_ROOT."""
    abs_path = _sanitize(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError("directory not found")
    if not os.path.isdir(abs_path):
        raise NotADirectoryError("not a directory")
    items: list[dict[str, object]] = []
    for entry in os.scandir(abs_path):
        rel = os.path.join(path, entry.name).replace("\\", "/").lstrip("/")
        try:
            st = entry.stat()
            size = 0 if entry.is_dir() else st.st_size
            modified = st.st_mtime
        except OSError:
            size = 0
            modified = 0.0
        items.append(
            {
                "name": entry.name,
                "path": rel,
                "is_dir": entry.is_dir(),
                "size": size,
                "modified": modified,
            }
        )
    return sorted(items, key=lambda x: (not x["is_dir"], str(x["name"]).lower()))


def _extract_episode_number(filename: str) -> int | None:
    stem = os.path.splitext(filename)[0]
    for pat in EPISODE_PATTERNS:
        m = pat.search(stem)
        if m:
            return int(m.group(m.lastindex))
    return None


def validate_episode_sequence(abs_path: str) -> dict[str, object]:
    """Проверка непрерывности нумерации серий в папке."""
    if not os.path.isdir(abs_path):
        return {
            "valid": True,
            "file_count": 1 if os.path.isfile(abs_path) else 0,
            "message": "Не папка — проверка серий пропущена",
        }

    video_files = [
        f.name
        for f in os.scandir(abs_path)
        if f.is_file() and os.path.splitext(f.name)[1].lower() in VIDEO_EXTENSIONS
    ]
    file_count = len(video_files)

    if file_count == 0:
        all_files = [f.name for f in os.scandir(abs_path) if f.is_file()]
        return {
            "valid": True,
            "file_count": len(all_files),
            "message": "Видеофайлы не найдены — проверка серий пропущена",
        }

    episodes = [
        ep for name in video_files if (ep := _extract_episode_number(name)) is not None
    ]
    if not episodes:
        return {
            "valid": True,
            "file_count": file_count,
            "message": "Не удалось определить номера серий — проверка пропущена",
        }

    episodes = sorted(set(episodes))
    expected = list(range(episodes[0], episodes[-1] + 1))
    missing = [x for x in expected if x not in episodes]
    if missing:
        return {
            "valid": False,
            "file_count": file_count,
            "message": (
                f"Пропущены серии: {missing}. Есть: {episodes[0]}-{episodes[-1]}, "
                f"найдено {len(episodes)} из {len(expected)}"
            ),
        }
    return {
        "valid": True,
        "file_count": file_count,
        "message": f"Серии идут по порядку ({episodes[0]}-{episodes[-1]}), всего {len(episodes)}",
    }


class _WorkerCancelled(Exception):
    """Отмена, поднимаемая внутри дочернего процесса хеширования."""


def _hash_worker(abs_src: str, piece_size: int, progress_q, cancel_ev) -> None:
    """Собрать .torrent целиком в ОТДЕЛЬНОМ процессе (spawn).

    Прогресс и результат отдаются через ``progress_q``:
      ("progress", pct) — очередной процент; ("done", bytes) — готовый torrent;
      ("cancelled", None) — отменено; ("error", str) — ошибка.
    Отмена приходит через ``cancel_ev`` (multiprocessing.Event)."""
    try:
        import libtorrent as lt  # noqa: PLC0415

        src = abs_src.replace("\\", "/")
        parent_dir = os.path.dirname(src)
        base_name = os.path.basename(src)

        fs = lt.file_storage()
        fs.set_name(base_name)
        lt.add_files(fs, src.encode("utf-8"))
        if fs.num_files() == 0:
            progress_q.put(("error", "no files to add"))
            return

        # v1-only: совместимость с оригинальным torrent_api и трекерами.
        flags = getattr(lt.create_torrent, "v1_only", 0)
        ct = lt.create_torrent(fs, piece_size, flags=flags)
        ct.set_creator("RelaySeed")
        ct.set_comment("Created by RelaySeed")

        num_pieces = ct.num_pieces() or 1
        last_report = [0.0]

        def hash_cb(idx: int) -> None:
            if cancel_ev.is_set():
                raise _WorkerCancelled()
            now = time.time()
            if now - last_report[0] >= 0.5:
                pct = int((idx + 1) * 100 / num_pieces)
                progress_q.put(("progress", min(pct, 99)))
                last_report[0] = now

        lt.set_piece_hashes(ct, parent_dir, hash_cb)
        if cancel_ev.is_set():
            progress_q.put(("cancelled", None))
            return

        entry = ct.generate()
        if not entry or b"info" not in entry:
            progress_q.put(("error", "torrent generation failed (no info dict)"))
            return
        progress_q.put(("done", lt.bencode(entry)))
    except _WorkerCancelled:
        progress_q.put(("cancelled", None))
    except Exception as exc:  # noqa: BLE001
        progress_q.put(("error", str(exc)))


class CreateStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CreateTask:
    id: int
    source_path: str
    save_path: str = ""
    status: CreateStatus = CreateStatus.QUEUED
    progress: int = 0
    message: str = "В очереди"
    error: str | None = None
    name: str = ""
    file_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    torrent_bytes: bytes | None = None

    def to_public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_path": self.source_path,
            "save_path": self.save_path,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "name": self.name,
            "file_count": self.file_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "has_torrent": self.torrent_bytes is not None,
        }


class TaskCancelled(Exception):
    pass


class CreatorService:
    """Управление задачами создания торрентов (in-memory, эфемерно)."""

    def __init__(self, max_workers: int | None = None, task_ttl: int | None = None) -> None:
        self._tasks: dict[int, CreateTask] = {}
        self._cancelled: set[int] = set()
        self._counter = 0
        self._lock = threading.Lock()
        workers = max_workers if max_workers is not None else _default_workers()
        # Потоки-надзиратели: каждый управляет одним дочерним процессом хеширования и
        # лишь перекачивает прогресс (в основном спит на queue.get → GIL свободен).
        self._executor = ThreadPoolExecutor(max_workers=workers)
        # Автоочистка: задача (и её .torrent в памяти) живёт не дольше TTL.
        self._ttl = task_ttl if task_ttl is not None else _task_ttl()
        self._stop = threading.Event()
        self._reaper = threading.Thread(
            target=self._reap_loop, name="creator-reaper", daemon=True
        )
        self._reaper.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _prune_locked(self, now: float | None = None) -> None:
        """Удалить задачи старше TTL. Вызывать под self._lock."""
        now = now if now is not None else time.time()
        stale = [tid for tid, t in self._tasks.items() if now - t.created_at > self._ttl]
        for tid in stale:
            self._tasks.pop(tid, None)
            self._cancelled.discard(tid)

    def _reap_loop(self) -> None:
        # Проверяем не реже раза в 10 минут (и не реже TTL).
        interval = min(self._ttl, 600)
        while not self._stop.wait(interval):
            with self._lock:
                self._prune_locked()

    def get(self, task_id: int) -> CreateTask | None:
        with self._lock:
            self._prune_locked()
            return self._tasks.get(task_id)

    def list_all(self) -> list[dict[str, object]]:
        """Все задачи (свежие сверху) — для очереди создания в UI."""
        with self._lock:
            self._prune_locked()
            tasks = sorted(self._tasks.values(), key=lambda t: t.id, reverse=True)
        return [t.to_public() for t in tasks]

    def delete(self, task_id: int) -> bool:
        """Удалить задачу из очереди (и её .torrent из памяти).

        Если задача ещё выполняется — сигналим дочернему процессу остановиться."""
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return False
            if task.status in (CreateStatus.QUEUED, CreateStatus.PROCESSING):
                self._cancelled.add(task_id)
        return True

    def create(self, source_path: str, skip_episode_check: bool = False) -> CreateTask:
        if _try_import_libtorrent() is None:
            raise RuntimeError("libtorrent is not available in this engine")
        abs_src = _sanitize(source_path)
        if not os.path.exists(abs_src):
            raise FileNotFoundError(f"source not found: {source_path}")

        file_count = 0
        if os.path.isdir(abs_src):
            if not skip_episode_check:
                validation = validate_episode_sequence(abs_src)
                if not validation["valid"]:
                    raise ValueError(str(validation["message"]))
            file_count = sum(1 for f in os.scandir(abs_src) if f.is_file())
        else:
            file_count = 1

        with self._lock:
            task_id = self._counter
            self._counter += 1
            task = CreateTask(
                id=task_id,
                source_path=source_path,
                save_path=os.path.dirname(abs_src.rstrip("/")),
                name=os.path.basename(abs_src.rstrip("/")),
                file_count=file_count,
            )
            self._tasks[task_id] = task
        self._executor.submit(self._run, task_id, abs_src)
        return task

    def cancel(self, task_id: int) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in (CreateStatus.QUEUED, CreateStatus.PROCESSING):
                return False
            self._cancelled.add(task_id)
            task.status = CreateStatus.CANCELLED
            task.message = "Отмена запрошена"
            task.updated_at = time.time()
        return True

    def _update(self, task_id: int, **fields: object) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            for key, val in fields.items():
                setattr(task, key, val)
            task.updated_at = time.time()

    def _run(self, task_id: int, abs_src: str) -> None:
        if _try_import_libtorrent() is None:
            self._update(
                task_id,
                status=CreateStatus.FAILED,
                error="libtorrent unavailable",
                message="Ошибка: libtorrent недоступен",
            )
            return
        try:
            if task_id in self._cancelled:
                raise TaskCancelled()
            self._update(
                task_id,
                status=CreateStatus.PROCESSING,
                message="Создание торрента",
                progress=0,
            )
            data = self._build_torrent(task_id, abs_src)
            if task_id in self._cancelled:
                raise TaskCancelled()
            self._update(
                task_id,
                status=CreateStatus.COMPLETED,
                progress=100,
                message="Готово",
                torrent_bytes=data,
            )
        except TaskCancelled:
            self._update(
                task_id,
                status=CreateStatus.CANCELLED,
                message="Отменено пользователем",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("create torrent task %s failed", task_id)
            self._update(
                task_id,
                status=CreateStatus.FAILED,
                error=str(exc),
                message=f"Ошибка: {exc}",
            )
        finally:
            with self._lock:
                self._cancelled.discard(task_id)

    def _build_torrent(self, task_id: int, abs_src: str) -> bytes:
        """Запустить хеширование в дочернем процессе и качать прогресс через очередь.

        Сам этот метод крутится в потоке-надзирателе: почти всё время спит на
        ``queue.get`` (GIL свободен) — событийный цикл движка не блокируется."""
        progress_q = _MP_CTX.Queue()
        cancel_ev = _MP_CTX.Event()
        proc = _MP_CTX.Process(
            target=_hash_worker,
            args=(abs_src, PIECE_SIZE, progress_q, cancel_ev),
            daemon=True,
        )
        proc.start()
        result: bytes | None = None
        try:
            while True:
                if task_id in self._cancelled and not cancel_ev.is_set():
                    cancel_ev.set()
                try:
                    kind, payload = progress_q.get(timeout=0.5)
                except queue.Empty:
                    if not proc.is_alive():
                        break
                    continue
                if kind == "progress":
                    pct = min(int(payload), 99)
                    self._update(
                        task_id,
                        progress=pct,
                        message=f"Хеширование: {pct}%",
                    )
                elif kind == "done":
                    result = payload
                    break
                elif kind == "cancelled":
                    raise TaskCancelled()
                elif kind == "error":
                    raise RuntimeError(str(payload))
        finally:
            cancel_ev.set()
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5)

        if result is None:
            if task_id in self._cancelled:
                raise TaskCancelled()
            raise RuntimeError(
                f"hash worker exited unexpectedly (code={proc.exitcode})"
            )
        return result
