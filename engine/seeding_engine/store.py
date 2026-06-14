from dataclasses import dataclass
from threading import Lock


@dataclass
class RuntimeHandle:
    db_id: int
    magnet_uri: str | None
    save_path: str
    runtime_status: str  # active | paused | error
    info_hash: str | None = None
    progress: float | None = None
    lt_state: str | None = None  # состояние libtorrent, если backend=libtorrent
    download_rate: int | None = None  # bytes/sec
    upload_rate: int | None = None  # bytes/sec
    total_uploaded: int | None = None  # bytes, за всё время раздачи
    peers: int | None = None
    # Расширенные метрики (паритет с ruTorrent)
    name: str | None = None
    size: int | None = None  # total_wanted, bytes
    downloaded: int | None = None  # all_time_download, bytes
    num_seeds: int | None = None
    ratio: float | None = None  # uploaded / downloaded
    eta: int | None = None  # секунды до завершения (None если раздача/неизвестно)
    added_time: int | None = None  # epoch seconds
    download_limit: int | None = None  # bytes/sec, 0 = без лимита
    upload_limit: int | None = None  # bytes/sec, 0 = без лимита


class RuntimeStore:
    """Потокобезопасное in-memory состояние; позже обвязка над libtorrent."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._by_id: dict[int, RuntimeHandle] = {}

    def upsert(self, db_id: int, magnet_uri: str | None, save_path: str) -> RuntimeHandle:
        with self._lock:
            h = RuntimeHandle(
                db_id=db_id,
                magnet_uri=magnet_uri,
                save_path=save_path,
                runtime_status="active",
            )
            self._by_id[db_id] = h
            return h

    def get(self, db_id: int) -> RuntimeHandle | None:
        with self._lock:
            return self._by_id.get(db_id)

    def list_all(self) -> list[RuntimeHandle]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda x: x.db_id)

    def set_paused(self, db_id: int, paused: bool) -> RuntimeHandle | None:
        with self._lock:
            h = self._by_id.get(db_id)
            if h is None:
                return None
            h.runtime_status = "paused" if paused else "active"
            return h

    def remove(self, db_id: int) -> bool:
        with self._lock:
            if db_id in self._by_id:
                del self._by_id[db_id]
                return True
            return False
