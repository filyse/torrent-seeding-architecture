"""Рантайм раздачи: libtorrent или in-memory mock (если биндинги недоступны)."""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from seeding_engine.store import RuntimeHandle, RuntimeStore

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _apply_libtorrent_session_settings(lt, ses) -> None:
    """DHT/LSD/UPnP/NAT-PMP и лимиты из env (разные версии биндингов — best effort)."""
    settings: dict[str, object] = {
        "enable_dht": _env_bool("LT_ENABLE_DHT", True),
        "enable_lsd": _env_bool("LT_ENABLE_LSD", True),
        "enable_upnp": _env_bool("LT_ENABLE_UPNP", True),
        "enable_natpmp": _env_bool("LT_ENABLE_NATPMP", True),
    }
    dl = os.getenv("LT_DOWNLOAD_RATE_LIMIT_BPS", "").strip()
    if dl != "":
        try:
            settings["download_rate_limit"] = int(dl)
        except ValueError:
            log.warning("LT_DOWNLOAD_RATE_LIMIT_BPS ignored (not int): %s", dl)
    ul = os.getenv("LT_UPLOAD_RATE_LIMIT_BPS", "").strip()
    if ul != "":
        try:
            settings["upload_rate_limit"] = int(ul)
        except ValueError:
            log.warning("LT_UPLOAD_RATE_LIMIT_BPS ignored (not int): %s", ul)
    cl = os.getenv("LT_CONNECTIONS_LIMIT", "").strip()
    if cl != "":
        try:
            settings["connections_limit"] = int(cl)
        except ValueError:
            log.warning("LT_CONNECTIONS_LIMIT ignored (not int): %s", cl)

    try:
        if hasattr(ses, "apply_settings"):
            ses.apply_settings(settings)
            return
        if hasattr(ses, "set_settings") and hasattr(lt, "session_settings"):
            st = lt.session_settings()
            for key, val in settings.items():
                if hasattr(st, key):
                    setattr(st, key, val)
            ses.set_settings(st)
    except Exception as exc:  # noqa: BLE001
        log.warning("libtorrent session settings not fully applied: %s", exc)


def _try_import_libtorrent():
    try:
        import libtorrent as lt  # type: ignore

        return lt
    except ImportError:
        return None


class TorrentRuntime(ABC):
    backend_name: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def add_torrent(self, db_id: int, magnet_uri: str | None, save_path: str) -> RuntimeHandle: ...

    @abstractmethod
    async def pause(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def resume(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def get(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def list_all(self) -> list[RuntimeHandle]: ...

    @abstractmethod
    async def remove(self, db_id: int) -> bool: ...


class MockTorrentRuntime(TorrentRuntime):
    backend_name = "mock"

    def __init__(self) -> None:
        self._store = RuntimeStore()

    async def start(self) -> None:
        log.info("engine backend=mock (no libtorrent)")

    async def stop(self) -> None:
        pass

    async def add_torrent(self, db_id: int, magnet_uri: str | None, save_path: str) -> RuntimeHandle:
        return self._store.upsert(db_id, magnet_uri, save_path)

    async def pause(self, db_id: int) -> RuntimeHandle | None:
        return self._store.set_paused(db_id, True)

    async def resume(self, db_id: int) -> RuntimeHandle | None:
        return self._store.set_paused(db_id, False)

    async def get(self, db_id: int) -> RuntimeHandle | None:
        return self._store.get(db_id)

    async def list_all(self) -> list[RuntimeHandle]:
        return self._store.list_all()

    async def remove(self, db_id: int) -> bool:
        return self._store.remove(db_id)


def _state_label(lt, st) -> str:
    s = st.state
    if hasattr(s, "name"):
        return str(s.name)
    try:
        for name in dir(lt.torrent_status):
            if not name.startswith("state_"):
                continue
            if getattr(lt.torrent_status, name, None) == s:
                return name.removeprefix("state_")
    except Exception:  # noqa: BLE001
        pass
    return str(s)


class LibtorrentTorrentRuntime(TorrentRuntime):
    backend_name = "libtorrent"

    def __init__(self) -> None:
        self._lt = _try_import_libtorrent()
        if self._lt is None:
            raise RuntimeError("libtorrent not importable")
        self._ses = None
        self._lock = asyncio.Lock()
        self._handles: dict[int, object] = {}
        self._meta: dict[int, tuple[str | None, str]] = {}
        self._listen_low = int(os.getenv("LT_LISTEN_PORT_LOW", "6881"))
        self._listen_high = int(os.getenv("LT_LISTEN_PORT_HIGH", "6889"))
        raw_state = os.getenv("SEEDING_LT_STATE_FILE", "").strip()
        self._state_path: str | None = raw_state or None

    async def start(self) -> None:
        lt = self._lt
        state_path = self._state_path

        def _mk():
            s = lt.session()
            if state_path and Path(state_path).is_file():
                try:
                    blob = Path(state_path).read_bytes()
                    s.load_state(lt.bdecode(blob))
                except Exception as exc:  # noqa: BLE001
                    log.warning("libtorrent load_state: %s", exc)
            s.listen_on(self._listen_low, self._listen_high)
            _apply_libtorrent_session_settings(lt, s)
            return s

        async with self._lock:
            if self._ses is not None:
                return
            self._ses = await asyncio.to_thread(_mk)
        log.info("libtorrent session started listen %s-%s", self._listen_low, self._listen_high)

    async def stop(self) -> None:
        """Остановка сессии; при SEEDING_LT_STATE_FILE — сохранение состояния (best effort)."""
        lt = self._lt
        state_path = self._state_path
        async with self._lock:
            ses = self._ses
            self._ses = None
            self._handles.clear()
            self._meta.clear()
            if ses is None:
                return

        def _shutdown(ses_inner=ses):
            if state_path:
                try:
                    blob = lt.bencode(ses_inner.save_state())
                    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(state_path).write_bytes(blob)
                    log.info("libtorrent session state written to %s", state_path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("libtorrent save_state: %s", exc)
            try:
                ses_inner.pause()
            except Exception as exc:  # noqa: BLE001
                log.warning("session pause: %s", exc)
            try:
                if hasattr(ses_inner, "abort"):
                    ses_inner.abort()
            except Exception as exc:  # noqa: BLE001
                log.debug("session abort: %s", exc)

        await asyncio.to_thread(_shutdown)
        log.info("libtorrent session stopped")

    async def add_torrent(self, db_id: int, magnet_uri: str | None, save_path: str) -> RuntimeHandle:
        if not magnet_uri or not magnet_uri.startswith("magnet:"):
            raise ValueError("magnet_uri required and must start with magnet:")
        lt = self._lt
        async with self._lock:
            if self._ses is None:
                raise RuntimeError("session not started")
            ses = self._ses

        Path(save_path).mkdir(parents=True, exist_ok=True)

        def _add():
            p = lt.add_torrent_params()
            lt.parse_magnet_uri(magnet_uri, p)
            p.save_path = save_path
            return ses.add_torrent(p)

        try:
            h = await asyncio.to_thread(_add)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"add_torrent failed: {exc}") from exc

        async with self._lock:
            self._handles[db_id] = h
            self._meta[db_id] = (magnet_uri, save_path)
        return await self._snapshot(db_id)

    async def _snapshot(self, db_id: int) -> RuntimeHandle:
        lt = self._lt
        async with self._lock:
            h = self._handles.get(db_id)
            meta = self._meta.get(db_id)
        if h is None or meta is None:
            raise KeyError(db_id)
        magnet_uri, save_path = meta

        def _read():
            st = h.status() if callable(getattr(h, "status", None)) else h.status
            ih_raw = h.info_hash() if callable(getattr(h, "info_hash", None)) else h.info_hash
            ih = str(ih_raw)
            paused = bool(getattr(st, "paused", False))
            if not paused and hasattr(lt, "torrent_flags"):
                try:
                    paused = bool(h.flags() & lt.torrent_flags.paused)
                except Exception:  # noqa: BLE001
                    pass
            return st, str(ih), paused

        st, ih_hex, paused = await asyncio.to_thread(_read)
        zero = "0" * 40
        if not ih_hex or ih_hex == zero:
            ih_hex = None
        return RuntimeHandle(
            db_id=db_id,
            magnet_uri=magnet_uri,
            save_path=save_path,
            runtime_status="paused" if paused else "active",
            info_hash=ih_hex,
            progress=float(st.progress),
            lt_state=_state_label(lt, st),
        )

    async def pause(self, db_id: int) -> RuntimeHandle | None:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        await asyncio.to_thread(h.pause)
        return await self._snapshot(db_id)

    async def resume(self, db_id: int) -> RuntimeHandle | None:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        await asyncio.to_thread(h.resume)
        return await self._snapshot(db_id)

    async def get(self, db_id: int) -> RuntimeHandle | None:
        async with self._lock:
            ok = db_id in self._handles
        if not ok:
            return None
        try:
            return await self._snapshot(db_id)
        except KeyError:
            return None

    async def list_all(self) -> list[RuntimeHandle]:
        async with self._lock:
            ids = sorted(self._handles.keys())
        out: list[RuntimeHandle] = []
        for i in ids:
            try:
                out.append(await self._snapshot(i))
            except KeyError:
                continue
        return out

    async def remove(self, db_id: int) -> bool:
        async with self._lock:
            if self._ses is None:
                return False
            h = self._handles.pop(db_id, None)
            self._meta.pop(db_id, None)
            ses = self._ses
        if h is None:
            return False

        def _rm():
            try:
                ses.remove_torrent(h)
            except Exception as exc:  # noqa: BLE001
                log.warning("remove_torrent db_id=%s: %s", db_id, exc)

        await asyncio.to_thread(_rm)
        return True


def build_torrent_runtime() -> TorrentRuntime:
    mode = os.getenv("SEEDING_ENGINE_BACKEND", "auto").strip().lower()
    if mode == "mock":
        return MockTorrentRuntime()
    if mode == "libtorrent":
        if _try_import_libtorrent() is None:
            raise RuntimeError("SEEDING_ENGINE_BACKEND=libtorrent but libtorrent is not installed")
        return LibtorrentTorrentRuntime()
    if _try_import_libtorrent() is None:
        log.warning("libtorrent not found, using mock engine backend")
        return MockTorrentRuntime()
    return LibtorrentTorrentRuntime()
