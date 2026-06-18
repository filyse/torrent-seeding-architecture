"""Рантайм раздачи: libtorrent или in-memory mock (если биндинги недоступны)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from seeding_engine.fastresume_io import (
    delete_fastresume,
    ensure_engine_dirs,
    fastresume_dir,
    fastresume_path,
    save_fastresume,
    session_state_path,
    try_read_resume_params,
)
from seeding_engine.store import RuntimeHandle, RuntimeStore

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        log.warning("%s ignored (not int): %s", name, raw)
        return default


def _clear_auto_managed_params(lt, p) -> None:
    """Сидбокс: ручное управление паузой. Снимаем auto_managed и paused у add_torrent_params,
    чтобы libtorrent не ставил готовые сиды на паузу сам и пауза от пользователя «прилипала»."""
    tf = getattr(lt, "torrent_flags", None)
    if tf is None:
        if hasattr(p, "auto_managed"):
            p.auto_managed = False
        if hasattr(p, "paused"):
            p.paused = False
        return
    try:
        flags = p.flags
        if hasattr(tf, "auto_managed"):
            flags &= ~tf.auto_managed
        if hasattr(tf, "paused"):
            flags &= ~tf.paused
        p.flags = flags
    except Exception as exc:  # noqa: BLE001
        log.warning("clear auto_managed on params failed: %s", exc)


# Эвристика «приватного» трекера: многие ру-трекеры (rudub и т.п.) не ставят флаг
# private в метаданных, но используют passkey/authkey. Для таких раздач тоже нужно
# глушить DHT/PEX/LSD, иначе libtorrent тянет мусорные пиры из открытых сетей.
_PRIVATE_TRACKER_HINTS = (
    "passkey=",
    "authkey=",
    "torrent_pass=",
    "secret=",
    "/rss/",
    "/announce.php",
)


def _urls_look_private(urls) -> bool:
    for url in urls or ():
        low = str(url or "").lower()
        for hint in _PRIVATE_TRACKER_HINTS:
            if hint in low:
                return True
    return False


def _ti_is_private(ti) -> bool:
    """Раздача приватная, если выставлен флаг private ИЛИ трекер выглядит приватным."""
    if ti is None:
        return False
    try:
        if bool(ti.priv()):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        urls = [t.url for t in ti.trackers()]
    except Exception:  # noqa: BLE001
        urls = []
    return _urls_look_private(urls)


def _tflag(lt, name):
    tf = getattr(lt, "torrent_flags", None)
    return getattr(tf, name, None) if tf is not None else None


def _private_flags_mask(lt) -> int:
    """Битовая маска disable_dht|disable_pex|disable_lsd (то, что есть в биндинге)."""
    tf = getattr(lt, "torrent_flags", None)
    if tf is None:
        return 0
    mask = 0
    for name in ("disable_dht", "disable_pex", "disable_lsd"):
        flag = getattr(tf, name, None)
        if flag is not None:
            mask |= int(flag)
    return mask


def _effective_disable_mask(lt, private: bool, net: dict | None) -> int:
    """Per-torrent маска отключений с учётом приватности и глобальной политики.

    DHT/LSD глобально гасятся на уровне сессии (enable_dht/enable_lsd), поэтому в
    per-torrent маску они попадают только для приватных раздач. У PEX глобального
    переключателя сессии в libtorrent 2.0 нет — поэтому глобальное отключение PEX
    эмулируем per-torrent флагом disable_pex."""
    net = net or {}
    mask = 0
    d = _tflag(lt, "disable_dht")
    if d is not None and private:
        mask |= int(d)
    ls = _tflag(lt, "disable_lsd")
    if ls is not None and private:
        mask |= int(ls)
    px = _tflag(lt, "disable_pex")
    if px is not None and (private or not net.get("pex", True)):
        mask |= int(px)
    return mask


def _handle_is_private(lt, h) -> bool:
    ti = None
    try:
        ti = h.torrent_file()
    except Exception:  # noqa: BLE001
        ti = None
    if _ti_is_private(ti):
        return True
    try:
        urls = []
        for t in h.trackers():
            if isinstance(t, dict):
                urls.append(t.get("url", ""))
            else:
                urls.append(getattr(t, "url", ""))
    except Exception:  # noqa: BLE001
        urls = []
    return _urls_look_private(urls)


def _params_is_private(lt, p) -> bool:
    priv = _ti_is_private(getattr(p, "ti", None))
    if not priv:
        try:
            urls = list(getattr(p, "trackers", []) or [])
        except Exception:  # noqa: BLE001
            urls = []
        priv = _urls_look_private(urls)
    return priv


def _apply_private_to_params(lt, p, net: dict | None = None) -> bool:
    """Выставить per-torrent отключения на add_torrent_params с учётом приватности
    раздачи и глобальной политики DHT/PEX/LSD."""
    priv = _params_is_private(lt, p)
    mask = _effective_disable_mask(lt, priv, net)
    if not mask:
        return False
    try:
        p.flags |= mask
    except Exception as exc:  # noqa: BLE001
        log.warning("apply private flags on params failed: %s", exc)
        return False
    if priv:
        log.info("private torrent detected -> DHT/PEX/LSD disabled")
    return True


def _apply_libtorrent_session_settings(lt, ses) -> None:
    """DHT/LSD/UPnP/NAT-PMP и лимиты из env (разные версии биндингов — best effort)."""
    listen_ifs = os.getenv("LT_LISTEN_INTERFACES", "0.0.0.0:51413,[::]:51413").strip()
    settings: dict[str, object] = {
        "enable_dht": _env_bool("LT_ENABLE_DHT", True),
        "enable_lsd": _env_bool("LT_ENABLE_LSD", True),
        "enable_upnp": _env_bool("LT_ENABLE_UPNP", True),
        "enable_natpmp": _env_bool("LT_ENABLE_NATPMP", True),
        # Сидбокс: не даём auto-manager ставить сиды на паузу сверх лимитов.
        # -1 = без ограничения. Это и есть корневой фикс «после рестарта часть в паузе».
        "active_seeds": _env_int("LT_ACTIVE_SEEDS", -1),
        "active_downloads": _env_int("LT_ACTIVE_DOWNLOADS", -1),
        "active_limit": _env_int("LT_ACTIVE_LIMIT", -1),
        "dont_count_slow_torrents": _env_bool("LT_DONT_COUNT_SLOW", True),
    }
    if listen_ifs:
        settings["listen_interfaces"] = listen_ifs
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
    async def add_torrent(
        self,
        db_id: int,
        magnet_uri: str | None,
        save_path: str,
        torrent_data: bytes | None = None,
    ) -> RuntimeHandle: ...

    @abstractmethod
    async def pause(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def resume(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def get(self, db_id: int) -> RuntimeHandle | None: ...

    @abstractmethod
    async def list_all(self) -> list[RuntimeHandle]: ...

    @abstractmethod
    async def remove(
        self,
        db_id: int,
        *,
        delete_files: bool = False,
        save_path: str | None = None,
        display_name: str | None = None,
    ) -> bool: ...

    async def list_peers(self, db_id: int) -> list[dict[str, object]]:
        return []

    async def list_files(self, db_id: int) -> list[dict[str, object]]:
        return []

    async def set_file_priorities(self, db_id: int, priorities: dict[int, int]) -> bool:
        return False

    async def list_trackers(self, db_id: int) -> list[dict[str, object]]:
        return []

    async def recheck(self, db_id: int) -> bool:
        return False

    async def reannounce(self, db_id: int) -> bool:
        return False

    async def set_limits(
        self, db_id: int, download_limit: int | None, upload_limit: int | None
    ) -> RuntimeHandle | None:
        return None

    async def set_private(
        self, db_id: int, on: bool | None = None
    ) -> RuntimeHandle | None:
        return None

    async def net_settings(self) -> dict[str, bool]:
        return {"dht": True, "pex": True, "lsd": True}

    async def set_net(
        self, dht: bool | None = None, pex: bool | None = None, lsd: bool | None = None
    ) -> dict[str, bool]:
        return await self.net_settings()

    async def add_tracker(self, db_id: int, url: str) -> bool:
        return False

    async def remove_tracker(self, db_id: int, url: str) -> bool:
        return False

    async def session_stats(self) -> dict[str, object]:
        return {}

    async def net_status(self) -> dict[str, object]:
        return {}

    async def set_session_limits(
        self, download_limit: int | None, upload_limit: int | None
    ) -> dict[str, object]:
        return {}


class MockTorrentRuntime(TorrentRuntime):
    backend_name = "mock"

    def __init__(self) -> None:
        self._store = RuntimeStore()

    async def start(self) -> None:
        log.info("engine backend=mock (no libtorrent)")

    async def stop(self) -> None:
        pass

    async def add_torrent(
        self,
        db_id: int,
        magnet_uri: str | None,
        save_path: str,
        torrent_data: bytes | None = None,
    ) -> RuntimeHandle:
        return self._store.upsert(db_id, magnet_uri, save_path)

    async def pause(self, db_id: int) -> RuntimeHandle | None:
        return self._store.set_paused(db_id, True)

    async def resume(self, db_id: int) -> RuntimeHandle | None:
        return self._store.set_paused(db_id, False)

    async def get(self, db_id: int) -> RuntimeHandle | None:
        return self._store.get(db_id)

    async def list_all(self) -> list[RuntimeHandle]:
        return self._store.list_all()

    async def remove(
        self,
        db_id: int,
        *,
        delete_files: bool = False,
        save_path: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        return self._store.remove(db_id)


def _peer_bool(peer, name: str) -> bool:
    val = getattr(peer, name, None)
    if val is None or callable(val):
        return False
    return bool(val)


def _format_peer_endpoint(peer) -> str:
    ip = getattr(peer, "ip", None)
    if ip is None:
        return ""
    if hasattr(ip, "address") and hasattr(ip, "port"):
        try:
            addr = ip.address()
            port = ip.port()
            return f"{addr}:{port}"
        except Exception:  # noqa: BLE001
            pass
    s = str(ip).strip()
    if s.startswith("(") and "," in s:
        import ast

        try:
            parts = ast.literal_eval(s)
            if isinstance(parts, (tuple, list)) and len(parts) >= 2:
                return f"{parts[0]}:{parts[1]}"
        except (SyntaxError, ValueError):
            pass
    return s


def _format_peer_flags(peer) -> str | None:
    labels: list[str] = []
    for name in (
        "seed",
        "upload_only",
        "interesting",
        "choked",
        "remote_interested",
        "remote_choked",
        "outgoing_connection",
        "local_connection",
        "utp_socket",
        "ssl_socket",
        "holepunched",
        "connecting",
    ):
        if _peer_bool(peer, name):
            labels.append(name)
    if labels:
        return ", ".join(labels)
    flags = getattr(peer, "flags", None)
    if flags is not None:
        try:
            return hex(int(flags))
        except (TypeError, ValueError):
            return str(flags)
    return None


def _peer_to_dict(peer) -> dict[str, object]:
    progress: float | None = None
    if hasattr(peer, "progress"):
        try:
            progress = float(peer.progress)
        except (TypeError, ValueError):
            progress = None
    elif hasattr(peer, "progress_ppm"):
        try:
            progress = float(peer.progress_ppm) / 1_000_000.0
        except (TypeError, ValueError):
            progress = None

    down = int(
        getattr(peer, "down_speed", 0)
        or getattr(peer, "payload_down_speed", 0)
        or getattr(peer, "download_rate", 0)
        or 0
    )
    up = int(
        getattr(peer, "up_speed", 0)
        or getattr(peer, "payload_up_speed", 0)
        or getattr(peer, "upload_rate", 0)
        or 0
    )
    client = str(getattr(peer, "client", "") or "").strip() or None
    source = str(getattr(peer, "source", "") or "").strip() or None
    return {
        "endpoint": _format_peer_endpoint(peer),
        "client": client,
        "progress": progress,
        "download_rate": down,
        "upload_rate": up,
        "flags": _format_peer_flags(peer),
        "source": source,
    }


def _total_bytes_from_status(st, *names: str) -> int | None:
    for name in names:
        val = getattr(st, name, None)
        if val is None:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            return n
    return None


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
        # Прогресс активного импорта (перенос с другого движка): db_id -> {phase, copied, total}.
        self._migrate_progress: dict[int, dict] = {}
        # Метаданные сетевого импорта, ожидающего поток контента: db_id -> {save_path, torrent_data, ...}.
        self._staged_imports: dict[int, dict] = {}
        self._listen_low = int(os.getenv("LT_LISTEN_PORT_LOW", "51413"))
        self._listen_high = int(os.getenv("LT_LISTEN_PORT_HIGH", "51413"))
        sp = session_state_path()
        self._state_path: str | None = str(sp) if sp else None
        # Периодическое сохранение fastresume/session.state — устойчивость к падению/kill -9.
        self._save_interval = _env_int("SEEDING_FASTRESUME_SAVE_INTERVAL", 300)
        self._save_task: asyncio.Task | None = None
        # Глобальная политика поиска пиров (переопределяется оркестратором при регистрации).
        self._net = {
            "dht": _env_bool("LT_ENABLE_DHT", True),
            "pex": _env_bool("LT_ENABLE_PEX", True),
            "lsd": _env_bool("LT_ENABLE_LSD", True),
        }

    async def start(self) -> None:
        lt = self._lt
        state_path = self._state_path
        ensure_engine_dirs()

        def _mk():
            s = lt.session()
            if state_path and Path(state_path).is_file():
                try:
                    blob = Path(state_path).read_bytes()
                    s.load_state(lt.bdecode(blob))
                except Exception as exc:  # noqa: BLE001
                    log.warning("libtorrent load_state: %s", exc)
            _apply_libtorrent_session_settings(lt, s)
            if not os.getenv("LT_LISTEN_INTERFACES", "").strip():
                s.listen_on(self._listen_low, self._listen_high)
            return s

        async with self._lock:
            if self._ses is not None:
                return
            self._ses = await asyncio.to_thread(_mk)
        log.info("libtorrent session started listen %s-%s", self._listen_low, self._listen_high)

        if _env_bool("SEEDING_ENGINE_SELF_RESTORE", True):
            try:
                n = await self._self_restore_from_disk()
                if n:
                    log.info("engine self-restore: loaded %s torrent(s) from disk on start", n)
            except Exception as exc:  # noqa: BLE001
                log.warning("engine self-restore failed: %s", exc)

        if self._save_interval > 0 and self._save_task is None:
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            log.info("periodic fastresume save every %ss", max(self._save_interval, 30))

    async def _save_all_to_disk(self) -> int:
        """Сохранить fastresume всех раздач + session.state. Reused периодикой и stop()."""
        lt = self._lt
        async with self._lock:
            ses = self._ses
            handles = dict(self._handles)
        if ses is None:
            return 0
        saved = 0
        for db_id, h in handles.items():
            try:
                await asyncio.to_thread(save_fastresume, lt, h, db_id)
                saved += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("save_fastresume db_id=%s: %s", db_id, exc)
        if self._state_path:
            state_path = self._state_path

            def _save_state(ses_inner=ses):
                try:
                    blob = lt.bencode(ses_inner.save_state())
                    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(state_path).write_bytes(blob)
                except Exception as exc:  # noqa: BLE001
                    log.warning("save_state: %s", exc)

            await asyncio.to_thread(_save_state)
        return saved

    async def _periodic_save_loop(self) -> None:
        interval = max(self._save_interval, 30)
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    n = await self._save_all_to_disk()
                    log.debug("periodic fastresume save: %s torrent(s)", n)
                except Exception as exc:  # noqa: BLE001
                    log.warning("periodic save loop iteration failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        """Остановка сессии; при SEEDING_LT_STATE_FILE — сохранение состояния (best effort)."""
        lt = self._lt
        state_path = self._state_path
        if self._save_task is not None:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._save_task = None
        async with self._lock:
            ses = self._ses
            handles = dict(self._handles)
            self._ses = None
            self._handles.clear()
            self._meta.clear()
            if ses is None:
                return

        for db_id, h in handles.items():
            await asyncio.to_thread(save_fastresume, lt, h, db_id)

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

    async def add_torrent(
        self,
        db_id: int,
        magnet_uri: str | None,
        save_path: str,
        torrent_data: bytes | None = None,
    ) -> RuntimeHandle:
        has_magnet = bool(magnet_uri and magnet_uri.startswith("magnet:"))
        has_file = bool(torrent_data)
        if not has_magnet and not has_file:
            raise ValueError("either magnet_uri or torrent_data is required")
        lt = self._lt
        async with self._lock:
            if self._ses is None:
                raise RuntimeError("session not started")
            ses = self._ses

        Path(save_path).mkdir(parents=True, exist_ok=True)

        fr = fastresume_path(db_id)
        if fr.is_file():
            try:
                h = await self._add_from_fastresume(db_id, save_path, magnet_uri, fr)
                async with self._lock:
                    self._handles[db_id] = h
                    self._meta[db_id] = (magnet_uri, save_path)
                return await self._snapshot(db_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("fastresume add db_id=%s failed, fallback: %s", db_id, exc)

        def _add():
            if has_magnet and magnet_uri:
                # libtorrent 2.x: parse_magnet_uri(str) -> add_torrent_params
                try:
                    p = lt.parse_magnet_uri(magnet_uri)
                except TypeError:
                    p = lt.add_torrent_params()
                    lt.parse_magnet_uri(magnet_uri, p)
            else:
                p = lt.add_torrent_params()
                try:
                    p.ti = lt.torrent_info(lt.bdecode(torrent_data))
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"invalid .torrent data: {exc}") from exc
            p.save_path = save_path
            _clear_auto_managed_params(lt, p)
            _apply_private_to_params(lt, p, self._net)
            return ses.add_torrent(p)

        try:
            h = await asyncio.to_thread(_add)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"add_torrent failed: {exc}") from exc

        def _kickstart(handle):
            if hasattr(handle, "resume"):
                handle.resume()
            if hasattr(handle, "force_reannounce"):
                try:
                    handle.force_reannounce(0)
                except TypeError:
                    handle.force_reannounce()

        await asyncio.to_thread(_kickstart, h)

        if has_file and torrent_data:
            await asyncio.to_thread(self._persist_torrent_file, db_id, torrent_data)

        async with self._lock:
            self._handles[db_id] = h
            self._meta[db_id] = (magnet_uri, save_path)
        await asyncio.to_thread(save_fastresume, lt, h, db_id)
        return await self._snapshot(db_id)

    async def _add_from_fastresume(
        self,
        db_id: int,
        save_path: str,
        magnet_uri: str | None,
        fr_path: Path,
    ):
        lt = self._lt
        async with self._lock:
            if self._ses is None:
                raise RuntimeError("session not started")
            ses = self._ses

        blob = fr_path.read_bytes()

        def _add():
            params = try_read_resume_params(lt, blob, save_path)
            if params is None:
                raise ValueError("invalid fastresume data")
            return ses.add_torrent(params)

        h = await asyncio.to_thread(_add)

        def _kickstart(handle):
            if hasattr(handle, "resume"):
                handle.resume()

        await asyncio.to_thread(_kickstart, h)
        log.info("restored db_id=%s from fastresume %s", db_id, fr_path)
        return h

    def _torrent_files_dir(self) -> Path:
        root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
        return root / ".torrents"

    async def read_torrent_file(self, db_id: int) -> bytes | None:
        """Сохранённый .torrent движка (для переноса раздачи на другой движок)."""
        path = self._torrent_files_dir() / f"{db_id}.torrent"
        if not path.is_file():
            return None
        return await asyncio.to_thread(path.read_bytes)

    @staticmethod
    def _torrent_name_from_data(lt, torrent_data: bytes) -> str:
        ti = lt.torrent_info(lt.bdecode(torrent_data))
        return str(ti.name())

    async def import_local(
        self,
        db_id: int,
        save_path: str,
        src_content_path: str,
        torrent_data: bytes,
    ) -> RuntimeHandle:
        """Принять раздачу с другого движка одной машины: скопировать контент из
        read-only `src_content_path` (виден через общий /media-mount) в свой том, затем
        добавить торрент и перепроверить хэш — на выходе honest seeding/downloading."""
        lt = self._lt
        if not torrent_data:
            raise ValueError("torrent_data is required for import")
        src = Path(src_content_path)
        if not src.exists():
            raise ValueError(f"source content not found: {src_content_path}")

        name = await asyncio.to_thread(self._torrent_name_from_data, lt, torrent_data)
        if not name:
            raise ValueError("cannot resolve torrent name from metadata")

        Path(save_path).mkdir(parents=True, exist_ok=True)
        dst = Path(save_path) / name

        total = await asyncio.to_thread(self._dir_size, src)
        self._migrate_progress[db_id] = {"phase": "copying", "copied": 0, "total": total}

        def _copy() -> None:
            # Инкрементально (по файлам): уже скопированные файлы с совпадающим размером
            # пропускаются — это делает перенос возобновляемым после сбоя без копии с нуля.
            prog = self._migrate_progress.get(db_id)
            self._copy_incremental(src, dst, prog)

        try:
            await asyncio.to_thread(_copy)
            self._migrate_progress[db_id] = {"phase": "checking", "copied": total, "total": total}
            handle = await self.add_torrent(db_id, None, save_path, torrent_data=torrent_data)
            # Перепроверяем скопированные данные: при совпадении пиров libtorrent поднимет до seeding.
            await self.recheck(db_id)
            return handle
        finally:
            # Прогресс копирования больше не нужен; recheck отслеживается через runtime snapshot.
            self._migrate_progress.pop(db_id, None)

    def migrate_progress(self, db_id: int) -> dict | None:
        """Снимок прогресса копирования контента при импорте (если идёт)."""
        prog = self._migrate_progress.get(db_id)
        return dict(prog) if prog is not None else None

    def stage_import(self, db_id: int, save_path: str, torrent_data: bytes, content_total: int) -> None:
        """Сохранить метаданные перед приёмом потока контента (см. import_remote)."""
        self._staged_imports[db_id] = {
            "save_path": save_path,
            "torrent_data": torrent_data,
            "content_total": int(content_total),
        }

    def pop_staged_import(self, db_id: int) -> dict | None:
        return self._staged_imports.pop(db_id, None)

    async def content_location(self, db_id: int) -> tuple[str, int] | None:
        """Путь к контенту раздачи на диске + суммарный размер (для сетевого переноса)."""
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None

        def _read() -> str | None:
            st = h.status() if callable(getattr(h, "status", None)) else h.status
            sp = str(getattr(st, "save_path", "") or "").strip()
            name = str(getattr(st, "name", "") or "").strip()
            if not sp or not name:
                return None
            return str(Path(sp) / name)

        path = await asyncio.to_thread(_read)
        if not path:
            return None
        total = await asyncio.to_thread(self._dir_size, Path(path))
        return path, total

    async def import_remote(
        self,
        db_id: int,
        save_path: str,
        torrent_data: bytes,
        content_total: int,
        src_iter: AsyncIterator[bytes],
    ) -> RuntimeHandle:
        """Принять раздачу с движка другой машины: распаковать tar-поток контента в свой том,
        затем add + recheck. Поток приходит из тела HTTP-запроса (оркестратор проксирует source)."""
        if not torrent_data:
            raise ValueError("torrent_data is required for import")
        Path(save_path).mkdir(parents=True, exist_ok=True)
        self._migrate_progress[db_id] = {"phase": "copying", "copied": 0, "total": int(content_total)}

        loop = asyncio.get_running_loop()
        rfd, wfd = os.pipe()

        def _extract() -> None:
            with os.fdopen(rfd, "rb") as rf, tarfile.open(fileobj=rf, mode="r|") as tf:
                # filter="data" блокирует path traversal и спец-файлы (Python 3.12+).
                tf.extractall(save_path, filter="data")

        extract_task = loop.run_in_executor(None, _extract)
        wf = os.fdopen(wfd, "wb")
        prog = self._migrate_progress.get(db_id)
        try:
            async for chunk in src_iter:
                if not chunk:
                    continue
                await loop.run_in_executor(None, wf.write, chunk)
                if prog is not None:
                    prog["copied"] = prog.get("copied", 0) + len(chunk)
        finally:
            try:
                wf.close()
            finally:
                await extract_task

        self._migrate_progress[db_id] = {
            "phase": "checking", "copied": int(content_total), "total": int(content_total),
        }
        try:
            handle = await self.add_torrent(db_id, None, save_path, torrent_data=torrent_data)
            await self.recheck(db_id)
            return handle
        finally:
            self._migrate_progress.pop(db_id, None)

    @staticmethod
    def _dir_size(src: Path) -> int:
        if src.is_file():
            try:
                return src.stat().st_size
            except OSError:
                return 0
        total = 0
        for root, _dirs, files in os.walk(src):
            for fn in files:
                try:
                    total += (Path(root) / fn).stat().st_size
                except OSError:
                    pass
        return total

    @staticmethod
    def _copy_incremental(src: Path, dst: Path, prog: dict | None) -> None:
        """Возобновляемое копирование по файлам: файл с совпадающим размером пропускается,
        остальные пишутся через `<имя>.part` + атомарный rename — частичная копия переживает
        обрыв, повтор докопирует недостающее (для отображения прогресса считаем байты)."""
        chunk = 4 * 1024 * 1024

        def _bump(n: int) -> None:
            if prog is not None:
                prog["copied"] = prog.get("copied", 0) + n

        def _copy_file(s: Path, d: Path) -> None:
            try:
                s_size = s.stat().st_size
            except OSError:
                s_size = 0
            if d.exists():
                try:
                    if d.stat().st_size == s_size:
                        _bump(s_size)  # уже на месте — пропускаем, но учитываем в прогрессе
                        return
                except OSError:
                    pass
            d.parent.mkdir(parents=True, exist_ok=True)
            part = d.with_name(d.name + ".part")
            with open(s, "rb") as fin, open(part, "wb") as fout:
                while True:
                    buf = fin.read(chunk)
                    if not buf:
                        break
                    fout.write(buf)
                    _bump(len(buf))
            try:
                shutil.copystat(s, part)
            except OSError:
                pass
            os.replace(part, d)

        if src.is_file():
            _copy_file(src, dst)
            return
        dst.mkdir(parents=True, exist_ok=True)
        for root, _dirs, files in os.walk(src):
            rel = Path(root).relative_to(src)
            target_dir = dst / rel
            target_dir.mkdir(parents=True, exist_ok=True)
            for fn in files:
                _copy_file(Path(root) / fn, target_dir / fn)

    # --- Возобновляемый сетевой перенос (per-file pull через оркестратор) ---
    @staticmethod
    def _safe_join(base: Path, rel: str) -> Path:
        """Склеить base + rel, не дав выйти за пределы base (защита от path traversal)."""
        target = (base / rel).resolve()
        base_r = base.resolve()
        if base_r != target and base_r not in target.parents:
            raise ValueError(f"unsafe path: {rel}")
        return target

    async def content_manifest(self, db_id: int) -> dict | None:
        """Список файлов контента раздачи (root + относительные пути и размеры)."""
        loc = await self.content_location(db_id)
        if loc is None:
            return None
        path, total = loc
        p = Path(path)

        def _scan() -> dict:
            files: list[dict] = []
            if p.is_file():
                files.append({"path": p.name, "size": p.stat().st_size})
                return {"root": p.name, "files": files, "total": total}
            for root, _dirs, fnames in os.walk(p):
                for fn in fnames:
                    fp = Path(root) / fn
                    try:
                        size = fp.stat().st_size
                    except OSError:
                        continue
                    files.append({"path": str(fp.relative_to(p)), "size": size})
            return {"root": p.name, "files": files, "total": total}

        return await asyncio.to_thread(_scan)

    async def content_file_path(self, db_id: int, rel: str) -> Path | None:
        """Абсолютный путь к файлу контента (для отдачи с поддержкой Range)."""
        loc = await self.content_location(db_id)
        if loc is None:
            return None
        base = Path(loc[0])
        if base.is_file():
            return base if rel in ("", base.name) else None
        return self._safe_join(base, rel)

    @staticmethod
    def import_file_size(save_path: str, root: str, rel: str) -> int:
        """Сколько байт файла уже лежит у приёмника (для возобновления с этого смещения)."""
        base = Path(save_path) / root
        try:
            target = LibtorrentTorrentRuntime._safe_join(base, rel)
        except ValueError:
            return 0
        try:
            return target.stat().st_size if target.is_file() else 0
        except OSError:
            return 0

    async def import_file_write(
        self, save_path: str, root: str, rel: str, offset: int, src_iter: AsyncIterator[bytes]
    ) -> int:
        """Дописать файл приёмника с указанного смещения (возобновляемый сетевой приём)."""
        base = Path(save_path) / root
        target = self._safe_join(base, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()

        def _open():
            f = open(target, "r+b" if target.exists() else "wb")
            f.seek(int(offset))
            f.truncate()
            return f

        f = await loop.run_in_executor(None, _open)
        written = 0
        try:
            async for chunk in src_iter:
                if not chunk:
                    continue
                await loop.run_in_executor(None, f.write, chunk)
                written += len(chunk)
        finally:
            await loop.run_in_executor(None, f.close)
        return written

    async def import_finalize(self, db_id: int) -> RuntimeHandle | None:
        """Завершить возобновляемый импорт: add + recheck по уже собранному на диске контенту."""
        staged = self._staged_imports.pop(db_id, None)
        if staged is None:
            return None
        handle = await self.add_torrent(
            db_id, None, staged["save_path"], torrent_data=staged["torrent_data"]
        )
        await self.recheck(db_id)
        return handle

    # --- Прямой перенос движок→движок (приёмник тянет контент у источника сам) ---
    @staticmethod
    def _peer_client(base_url: str, timeout) -> httpx.AsyncClient:
        """httpx-клиент к движку-источнику: общий токен X-Engine-Token + TLS-CA как у оркестратора."""
        token = os.getenv("SEEDING_ENGINE_API_TOKEN", "").strip()
        headers = {"X-Engine-Token": token} if token else None
        verify: object = True
        if base_url.startswith("https://"):
            ca = os.getenv("SEEDING_ENGINE_TLS_CA", "").strip()
            verify = ca if ca else True
        transport = httpx.AsyncHTTPTransport(verify=verify)
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport, headers=headers
        )

    async def peer_reachable(self, source_url: str) -> dict:
        """Проверить, видит ли этот движок источник напрямую (для авто-выбора direct)."""
        import time as _t

        timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
        t0 = _t.monotonic()
        try:
            async with self._peer_client(source_url, timeout) as client:
                r = await client.get("/internal/v1/health")
                r.raise_for_status()
            return {"reachable": True, "latency_ms": round((_t.monotonic() - t0) * 1000, 1)}
        except (httpx.HTTPError, OSError) as exc:
            return {"reachable": False, "error": str(exc)[:200]}

    async def import_direct(
        self, db_id: int, save_path: str, torrent_data: bytes, source_url: str
    ) -> RuntimeHandle:
        """Принять раздачу, СКАЧАВ контент напрямую у движка-источника (минуя оркестратор).

        Возобновляемо: пофайлово, уже принятые файлы/байты пропускаются (Range)."""
        if not torrent_data:
            raise ValueError("torrent_data is required for import")
        Path(save_path).mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        loop = asyncio.get_running_loop()

        async with self._peer_client(source_url, timeout) as client:
            mr = await client.get(f"/internal/v1/torrents/{db_id}/content-manifest")
            mr.raise_for_status()
            manifest = mr.json()
            root = str(manifest.get("root") or "")
            files = manifest.get("files") or []
            total = int(manifest.get("total") or sum(int(f.get("size") or 0) for f in files))
            self._migrate_progress[db_id] = {"phase": "copying", "copied": 0, "total": total}
            prog = self._migrate_progress[db_id]
            base = Path(save_path) / root

            for f in files:
                rel = str(f.get("path") or "")
                size = int(f.get("size") or 0)
                if not rel:
                    continue
                target = self._safe_join(base, rel)
                have = target.stat().st_size if target.is_file() else 0
                have = min(have, size)
                if size > 0 and have >= size:
                    prog["copied"] = prog.get("copied", 0) + size
                    continue
                prog["copied"] = prog.get("copied", 0) + have  # уже принятая часть
                target.parent.mkdir(parents=True, exist_ok=True)
                headers = {"Range": f"bytes={have}-"} if have > 0 else {}
                async with client.stream(
                    "GET", f"/internal/v1/torrents/{db_id}/content-file",
                    params={"path": rel}, headers=headers,
                ) as resp:
                    resp.raise_for_status()

                    def _open():
                        fobj = open(target, "r+b" if target.exists() else "wb")
                        fobj.seek(have)
                        fobj.truncate()
                        return fobj

                    fobj = await loop.run_in_executor(None, _open)
                    try:
                        async for chunk in resp.aiter_bytes():
                            await loop.run_in_executor(None, fobj.write, chunk)
                            prog["copied"] = prog.get("copied", 0) + len(chunk)
                    finally:
                        await loop.run_in_executor(None, fobj.close)

        self._migrate_progress[db_id] = {"phase": "checking", "copied": total, "total": total}
        try:
            handle = await self.add_torrent(db_id, None, save_path, torrent_data=torrent_data)
            await self.recheck(db_id)
            return handle
        finally:
            self._migrate_progress.pop(db_id, None)

    def _persist_torrent_file(self, db_id: int, torrent_data: bytes) -> None:
        d = self._torrent_files_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{db_id}.torrent"
        path.write_bytes(torrent_data)
        log.info("saved .torrent for db_id=%s at %s", db_id, path)

    def _delete_persisted_torrent_file(self, db_id: int) -> None:
        path = self._torrent_files_dir() / f"{db_id}.torrent"
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                log.warning("remove .torrent db_id=%s: %s", db_id, exc)

    @staticmethod
    def _libtorrent_delete_flags(lt, delete_files: bool) -> int:
        if not delete_files:
            return 0
        flags = 0
        for parent in (
            getattr(lt, "session", None),
            getattr(lt, "remove_flags_t", None),
            getattr(lt, "options_t", None),
        ):
            if parent is None:
                continue
            for name in ("delete_files", "delete_partfile"):
                if hasattr(parent, name):
                    flags |= int(getattr(parent, name))
        return flags

    @staticmethod
    def _guess_content_paths(save_path: str | None, display_name: str | None) -> list[Path]:
        if not save_path:
            return []
        root = Path(save_path)
        paths: list[Path] = []
        name = (display_name or "").strip()
        if name.lower().endswith(".torrent"):
            name = name[: -len(".torrent")]
        if name:
            paths.append(root / name)
        return paths

    async def _collect_paths_from_handle_async(self, h, save_path_fallback: str | None) -> list[Path]:
        def _read():
            out: list[Path] = []
            st = h.status() if callable(getattr(h, "status", None)) else h.status
            sp = str(getattr(st, "save_path", "") or save_path_fallback or "").strip()
            if not sp:
                return out
            root = Path(sp)
            tname = str(getattr(st, "name", "") or "").strip()
            if tname:
                out.append(root / tname)
            try:
                ti = h.torrent_file() if callable(getattr(h, "torrent_file", None)) else None
                if ti is not None:
                    files = ti.files()
                    for i in range(files.num_files()):
                        out.append(root / files.file_path(i))
            except Exception as exc:  # noqa: BLE001
                log.debug("torrent_file paths db_id: %s", exc)
            return out

        return await asyncio.to_thread(_read)

    @staticmethod
    def _delete_paths_on_disk(paths: list[Path], save_path: str | None) -> None:
        data_root = Path(os.getenv("SEEDING_DATA_ROOT", "/data"))
        try:
            data_root_resolved = data_root.resolve()
        except OSError:
            data_root_resolved = data_root
        save_root: Path | None = None
        if save_path:
            sp = Path(save_path)
            try:
                save_root = sp.resolve()
            except OSError:
                save_root = sp

        seen: set[Path] = set()
        candidates: list[Path] = []
        for p in paths:
            try:
                resolved = p.resolve()
            except OSError:
                resolved = p
            if resolved in seen:
                continue
            # Никогда не удалять корень тома (/data) или голый save_path — только файлы раздачи.
            if resolved == data_root_resolved or (save_root is not None and resolved == save_root):
                log.warning("skip unsafe delete path %s", resolved)
                continue
            seen.add(resolved)
            candidates.append(resolved)

        for path in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                    log.info("removed directory %s", path)
                else:
                    path.unlink()
                    log.info("removed file %s", path)
            except OSError as exc:
                log.warning("failed to remove %s: %s", path, exc)

    def _default_save_path(self) -> str:
        root = os.getenv("SEEDING_DATA_ROOT", "/data")
        sub = os.getenv("ENGINE_STORAGE_SUBDIR", "").strip()
        return str(Path(root) / sub) if sub else root

    def _save_path_from_resume(self, fr: Path) -> str:
        """save_path хранится внутри самого fastresume; читаем его, иначе дефолт движка."""
        lt = self._lt
        try:
            params = lt.read_resume_data(fr.read_bytes())
            sp = (getattr(params, "save_path", "") or "").strip()
            if sp:
                return sp
        except Exception:  # noqa: BLE001
            pass
        return self._default_save_path()

    async def _self_restore_from_disk(self) -> int:
        """При старте движка поднять все раздачи с собственного тома (fastresume + .torrents).
        Движок не читает БД — источник правды для рестарта движка это его собственный том,
        который зеркалит назначенные ему раздачи (создаётся при add, удаляется при remove)."""
        loaded = 0
        seen: set[int] = set()

        fr_dir = fastresume_dir()
        if fr_dir.is_dir():
            for fr in sorted(fr_dir.glob("*.fastresume")):
                try:
                    db_id = int(fr.stem)
                except ValueError:
                    continue
                async with self._lock:
                    if db_id in self._handles:
                        seen.add(db_id)
                        continue
                save_path = self._save_path_from_resume(fr)
                try:
                    h = await self._add_from_fastresume(db_id, save_path, None, fr)
                    async with self._lock:
                        self._handles[db_id] = h
                        self._meta[db_id] = (None, save_path)
                    seen.add(db_id)
                    loaded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("self-restore fastresume db_id=%s failed: %s", db_id, exc)

        tdir = self._torrent_files_dir()
        if tdir.is_dir():
            default_sp = self._default_save_path()
            for tf in sorted(tdir.glob("*.torrent")):
                try:
                    db_id = int(tf.stem)
                except ValueError:
                    continue
                if db_id in seen:
                    continue
                async with self._lock:
                    if db_id in self._handles:
                        continue
                try:
                    await self.add_torrent(db_id, None, default_sp, torrent_data=tf.read_bytes())
                    loaded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("self-restore torrent db_id=%s failed: %s", db_id, exc)

        return loaded

    async def restore_from_disk(self, db_id: int, save_path: str) -> RuntimeHandle | None:
        """Перезагрузить из fastresume или /data/.torrents/{id}.torrent (после рестарта движка)."""
        fr = fastresume_path(db_id)
        if fr.is_file():
            async with self._lock:
                if db_id in self._handles:
                    return await self._snapshot(db_id)
            try:
                h = await self._add_from_fastresume(db_id, save_path, None, fr)
                async with self._lock:
                    self._handles[db_id] = h
                    self._meta[db_id] = (None, save_path)
                return await self._snapshot(db_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("restore fastresume db_id=%s failed: %s", db_id, exc)

        path = self._torrent_files_dir() / f"{db_id}.torrent"
        if not path.is_file():
            return None
        data = path.read_bytes()
        async with self._lock:
            if db_id in self._handles:
                return await self._snapshot(db_id)
        return await self.add_torrent(db_id, None, save_path, torrent_data=data)

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
            dl_limit = None
            up_limit = None
            try:
                dl_limit = int(h.download_limit())
            except Exception:  # noqa: BLE001
                pass
            try:
                up_limit = int(h.upload_limit())
            except Exception:  # noqa: BLE001
                pass
            private = None
            tf = getattr(lt, "torrent_flags", None)
            disable_dht = getattr(tf, "disable_dht", None) if tf is not None else None
            if disable_dht is not None:
                try:
                    private = bool(h.flags() & disable_dht)
                except Exception:  # noqa: BLE001
                    private = None
            return st, str(ih), paused, dl_limit, up_limit, private

        st, ih_hex, paused, dl_limit, up_limit, private = await asyncio.to_thread(_read)
        zero = "0" * 40
        if not ih_hex or ih_hex == zero:
            ih_hex = None

        uploaded = _total_bytes_from_status(st, "total_upload", "all_time_upload")
        downloaded = _total_bytes_from_status(st, "all_time_download", "total_download")
        size = _total_bytes_from_status(st, "total_wanted", "total")
        done = _total_bytes_from_status(st, "total_wanted_done", "total_done")
        dl_rate = int(getattr(st, "download_payload_rate", 0) or 0)
        lt_state = _state_label(lt, st)

        ratio: float | None = None
        if uploaded is not None and downloaded:
            ratio = round(uploaded / downloaded, 4)
        # downloaded == 0 (импорт с сидбокса): рейтинг не определён → None (в UI «—»)

        eta: int | None = None
        if lt_state not in ("seeding", "finished") and dl_rate > 0 and size is not None and done is not None:
            remaining = size - done
            if remaining > 0:
                eta = int(remaining / dl_rate)

        added_time = None
        raw_added = getattr(st, "added_time", None)
        if raw_added is not None:
            try:
                added_time = int(raw_added)
            except (TypeError, ValueError):
                added_time = None

        return RuntimeHandle(
            db_id=db_id,
            magnet_uri=magnet_uri,
            save_path=save_path,
            runtime_status="paused" if paused else "active",
            info_hash=ih_hex,
            progress=float(st.progress),
            lt_state=lt_state,
            download_rate=dl_rate,
            upload_rate=int(getattr(st, "upload_payload_rate", 0) or 0),
            total_uploaded=uploaded,
            peers=int(getattr(st, "num_peers", 0) or 0),
            name=str(getattr(st, "name", "") or "") or None,
            size=size,
            downloaded=downloaded,
            num_seeds=int(getattr(st, "num_seeds", 0) or 0),
            ratio=ratio,
            eta=eta,
            added_time=added_time,
            download_limit=dl_limit,
            upload_limit=up_limit,
            private=private,
        )

    def _unset_auto_managed(self, h) -> None:
        lt = self._lt
        tf = getattr(lt, "torrent_flags", None)
        if tf is not None and hasattr(h, "unset_flags") and hasattr(tf, "auto_managed"):
            try:
                h.unset_flags(tf.auto_managed)
            except Exception as exc:  # noqa: BLE001
                log.warning("unset auto_managed failed: %s", exc)

    def _manual_pause(self, h) -> None:
        self._unset_auto_managed(h)
        h.pause()

    def _manual_resume(self, h) -> None:
        self._unset_auto_managed(h)
        h.resume()

    async def pause(self, db_id: int) -> RuntimeHandle | None:
        lt = self._lt
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        await asyncio.to_thread(self._manual_pause, h)
        await asyncio.to_thread(save_fastresume, lt, h, db_id)
        return await self._snapshot(db_id)

    async def resume(self, db_id: int) -> RuntimeHandle | None:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        await asyncio.to_thread(self._manual_resume, h)
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

    async def debug_torrent(self, db_id: int) -> dict | None:
        """Подробный статус libtorrent для диагностики (трекеры, ошибки, DHT)."""
        lt = self._lt
        async with self._lock:
            h = self._handles.get(db_id)
            ses = self._ses
        if h is None:
            return None

        def _collect():
            st = h.status() if callable(getattr(h, "status", None)) else h.status
            out: dict[str, object] = {
                "state": _state_label(lt, st),
                "progress": float(st.progress),
                "peers": int(getattr(st, "num_peers", 0) or 0),
                "seeds": int(getattr(st, "num_seeds", 0) or 0),
                "connections": int(getattr(st, "num_connections", 0) or 0),
                "download_rate": int(getattr(st, "download_payload_rate", 0) or 0),
                "upload_rate": int(getattr(st, "upload_payload_rate", 0) or 0),
                "current_tracker": str(getattr(st, "current_tracker", "") or ""),
                "message": str(getattr(st, "message", "") or ""),
            }
            errc = getattr(st, "errc", None)
            if errc is not None:
                out["errc"] = str(errc)
            if ses is not None and hasattr(ses, "status"):
                ss = ses.status() if callable(ses.status) else ses.status
                out["session"] = {
                    "listening_port": getattr(ss, "listening_port", None),
                    "dht_nodes": getattr(ss, "dht_nodes", None),
                    "has_incoming": getattr(ss, "has_incoming_connections", None),
                }
            trackers: list[dict[str, object]] = []
            if hasattr(h, "trackers"):
                try:
                    for tr in h.trackers():
                        trackers.append(
                            {
                                "url": str(getattr(tr, "url", tr)),
                                "state": str(getattr(tr, "state", "")),
                                "msg": str(getattr(tr, "message", "") or ""),
                                "peers": int(getattr(tr, "num_peers", 0) or 0),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    trackers.append({"error": str(exc)})
            out["trackers"] = trackers
            return out

        return await asyncio.to_thread(_collect)

    async def list_peers(self, db_id: int) -> list[dict[str, object]]:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "get_peer_info"):
            return []

        def _read() -> list[dict[str, object]]:
            try:
                raw = h.get_peer_info()
            except Exception as exc:  # noqa: BLE001
                log.debug("get_peer_info db_id=%s: %s", db_id, exc)
                return []
            return [_peer_to_dict(p) for p in raw]

        return await asyncio.to_thread(_read)

    async def list_files(self, db_id: int) -> list[dict[str, object]]:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return []

        def _read() -> list[dict[str, object]]:
            ti = h.torrent_file() if callable(getattr(h, "torrent_file", None)) else None
            if ti is None:
                return []  # метаданные ещё не получены (magnet)
            files = ti.files()
            n = files.num_files()
            try:
                progress = list(h.file_progress())
            except Exception:  # noqa: BLE001
                progress = [0] * n
            try:
                prios = list(h.get_file_priorities())
            except Exception:  # noqa: BLE001
                try:
                    prios = list(h.file_priorities())
                except Exception:  # noqa: BLE001
                    prios = [4] * n
            out: list[dict[str, object]] = []
            for i in range(n):
                size = int(files.file_size(i))
                done = int(progress[i]) if i < len(progress) else 0
                out.append(
                    {
                        "index": i,
                        "path": str(files.file_path(i)),
                        "size": size,
                        "downloaded": done,
                        "progress": (done / size) if size > 0 else 1.0,
                        "priority": int(prios[i]) if i < len(prios) else 4,
                    }
                )
            return out

        return await asyncio.to_thread(_read)

    async def set_file_priorities(self, db_id: int, priorities: dict[int, int]) -> bool:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return False
        lt = self._lt

        def _apply() -> bool:
            ti = h.torrent_file() if callable(getattr(h, "torrent_file", None)) else None
            if ti is None:
                return False
            n = ti.files().num_files()
            try:
                cur = list(h.get_file_priorities())
            except Exception:  # noqa: BLE001
                cur = [4] * n
            for idx, prio in priorities.items():
                if 0 <= idx < n:
                    cur[idx] = max(0, min(7, int(prio)))
            try:
                h.prioritize_files(cur)
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("prioritize_files db_id=%s: %s", db_id, exc)
                return False

        ok = await asyncio.to_thread(_apply)
        if ok:
            await asyncio.to_thread(save_fastresume, lt, h, db_id)
        return ok

    async def list_trackers(self, db_id: int) -> list[dict[str, object]]:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "trackers"):
            return []

        def _read() -> list[dict[str, object]]:
            out: list[dict[str, object]] = []
            try:
                for tr in h.trackers():
                    out.append(
                        {
                            "url": str(tr.get("url") if isinstance(tr, dict) else getattr(tr, "url", tr)),
                            "tier": int(
                                tr.get("tier", 0) if isinstance(tr, dict) else getattr(tr, "tier", 0) or 0
                            ),
                            "message": str(
                                tr.get("message", "")
                                if isinstance(tr, dict)
                                else getattr(tr, "message", "") or ""
                            ),
                            "verified": bool(
                                tr.get("verified", False)
                                if isinstance(tr, dict)
                                else getattr(tr, "verified", False)
                            ),
                            "num_peers": int(
                                tr.get("num_peers", 0)
                                if isinstance(tr, dict)
                                else getattr(tr, "num_peers", 0) or 0
                            ),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("trackers db_id=%s: %s", db_id, exc)
            return out

        return await asyncio.to_thread(_read)

    async def recheck(self, db_id: int) -> bool:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "force_recheck"):
            return False
        await asyncio.to_thread(h.force_recheck)
        return True

    async def reannounce(self, db_id: int) -> bool:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "force_reannounce"):
            return False

        def _ann():
            try:
                h.force_reannounce(0)
            except TypeError:
                h.force_reannounce()

        await asyncio.to_thread(_ann)
        return True

    async def set_limits(
        self, db_id: int, download_limit: int | None, upload_limit: int | None
    ) -> RuntimeHandle | None:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        lt = self._lt

        def _apply():
            if download_limit is not None and hasattr(h, "set_download_limit"):
                h.set_download_limit(max(0, int(download_limit)))
            if upload_limit is not None and hasattr(h, "set_upload_limit"):
                h.set_upload_limit(max(0, int(upload_limit)))

        await asyncio.to_thread(_apply)
        await asyncio.to_thread(save_fastresume, lt, h, db_id)
        return await self._snapshot(db_id)

    async def set_private(
        self, db_id: int, on: bool | None = None
    ) -> RuntimeHandle | None:
        """Включить/выключить приватный режим раздачи (DHT/PEX/LSD).

        on=None — автоопределение по флагу private/passkey трекера."""
        lt = self._lt
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None:
            return None
        full = _private_flags_mask(lt)

        def _apply():
            priv = on
            if priv is None:
                priv = _handle_is_private(lt, h)
            want_off = _effective_disable_mask(lt, bool(priv), self._net)
            want_on = full & ~want_off  # биты, которые нужно снять
            if full and hasattr(h, "set_flags") and hasattr(h, "unset_flags"):
                if want_off:
                    h.set_flags(want_off)
                if want_on:
                    h.unset_flags(want_on)
            # переаннонс, чтобы изменения подхватились трекером/сессией
            if hasattr(h, "force_reannounce"):
                try:
                    h.force_reannounce(0)
                except TypeError:
                    h.force_reannounce()

        await asyncio.to_thread(_apply)
        await asyncio.to_thread(save_fastresume, lt, h, db_id)
        return await self._snapshot(db_id)

    async def net_settings(self) -> dict[str, bool]:
        return dict(self._net)

    async def set_net(
        self, dht: bool | None = None, pex: bool | None = None, lsd: bool | None = None
    ) -> dict[str, bool]:
        """Глобально включить/выключить DHT/PEX/LSD на этом движке.

        DHT/LSD — настройки сессии libtorrent (применяются ко всем раздачам сразу).
        PEX глобального переключателя сессии не имеет, поэтому проходим по всем
        раздачам и ставим/снимаем per-torrent флаг disable_pex (приватные остаются
        без PEX в любом случае)."""
        lt = self._lt
        if dht is not None:
            self._net["dht"] = bool(dht)
        if pex is not None:
            self._net["pex"] = bool(pex)
        if lsd is not None:
            self._net["lsd"] = bool(lsd)
        async with self._lock:
            ses = self._ses
            handles = list(self._handles.values())

        def _apply():
            if ses is not None and hasattr(ses, "apply_settings"):
                try:
                    ses.apply_settings(
                        {"enable_dht": self._net["dht"], "enable_lsd": self._net["lsd"]}
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("apply net session settings failed: %s", exc)
            px = _tflag(lt, "disable_pex")
            if px is None:
                return
            for h in handles:
                try:
                    priv = _handle_is_private(lt, h)
                    if (priv or not self._net["pex"]) and hasattr(h, "set_flags"):
                        h.set_flags(int(px))
                    elif hasattr(h, "unset_flags"):
                        h.unset_flags(int(px))
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.to_thread(_apply)
        return dict(self._net)

    async def add_tracker(self, db_id: int, url: str) -> bool:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "add_tracker"):
            return False
        url = url.strip()
        if not url:
            return False

        def _add():
            h.add_tracker({"url": url})

        await asyncio.to_thread(_add)
        return True

    async def remove_tracker(self, db_id: int, url: str) -> bool:
        async with self._lock:
            h = self._handles.get(db_id)
        if h is None or not hasattr(h, "trackers"):
            return False
        url = url.strip()
        if not url:
            return False

        def _remove() -> bool:
            kept: list[dict[str, object]] = []
            for tr in h.trackers():
                tr_url = str(tr.get("url") if isinstance(tr, dict) else getattr(tr, "url", tr))
                if tr_url == url:
                    continue
                tier = tr.get("tier", 0) if isinstance(tr, dict) else getattr(tr, "tier", 0) or 0
                kept.append({"url": tr_url, "tier": int(tier)})
            if hasattr(h, "replace_trackers"):
                h.replace_trackers(kept)
                return True
            if hasattr(h, "remove_tracker"):
                h.remove_tracker(url)
                return True
            return False

        return await asyncio.to_thread(_remove)

    async def session_stats(self) -> dict[str, object]:
        async with self._lock:
            ses = self._ses
            handles = dict(self._handles)
        if ses is None:
            return {"torrents": 0}

        def _collect(handles_inner=handles, ses_inner=ses) -> dict[str, object]:
            ss = ses_inner.status() if callable(getattr(ses_inner, "status", None)) else ses_inner.status
            dl_rate = int(getattr(ss, "download_rate", 0) or getattr(ss, "payload_download_rate", 0) or 0)
            up_rate = int(getattr(ss, "upload_rate", 0) or getattr(ss, "payload_upload_rate", 0) or 0)
            total_up = 0
            total_dl = 0
            active = 0
            for h in handles_inner.values():
                try:
                    st = h.status() if callable(getattr(h, "status", None)) else h.status
                    total_up += int(getattr(st, "total_upload", 0) or getattr(st, "all_time_upload", 0) or 0)
                    total_dl += int(getattr(st, "all_time_download", 0) or getattr(st, "total_download", 0) or 0)
                    if not getattr(st, "paused", False):
                        active += 1
                except Exception:  # noqa: BLE001
                    continue
            dl_lim = int(ses_inner.download_rate_limit()) if hasattr(ses_inner, "download_rate_limit") else 0
            up_lim = int(ses_inner.upload_rate_limit()) if hasattr(ses_inner, "upload_rate_limit") else 0
            # Порт слушателя отдаёт сам объект сессии (listen_port()), а не его status.
            listening_port = getattr(ss, "listening_port", None)
            if not listening_port and hasattr(ses_inner, "listen_port"):
                try:
                    lp = ses_inner.listen_port()
                    listening_port = int(lp) if lp else None
                except Exception:  # noqa: BLE001
                    listening_port = None
            return {
                "torrents": len(handles_inner),
                "torrents_active": active,
                "download_rate": dl_rate,
                "upload_rate": up_rate,
                "total_uploaded": total_up,
                "total_downloaded": total_dl,
                "download_limit": dl_lim,
                "upload_limit": up_lim,
                "dht_nodes": getattr(ss, "dht_nodes", None),
                "listening_port": listening_port,
            }

        return await asyncio.to_thread(_collect)

    async def net_status(self) -> dict[str, object]:
        """Сетевой статус движка для проверки связности при онбординге:
        слушает ли libtorrent порт, был ли хоть один входящий коннект (признак, что
        BT-порт доступен снаружи / NAT проброшен), DHT-узлы и число пиров."""
        async with self._lock:
            ses = self._ses
            handles = dict(self._handles)
        configured: int | None = None
        ifs = os.getenv("LT_LISTEN_INTERFACES", "").strip()
        if ":" in ifs:
            tail = ifs.split(",")[0].rsplit(":", 1)[-1]
            if tail.isdigit():
                configured = int(tail)
        if configured is None:
            configured = self._listen_low
        if ses is None:
            return {"configured_port": configured, "listening": False}

        def _collect(ses_inner=ses, handles_inner=handles) -> dict[str, object]:
            ss = ses_inner.status() if callable(getattr(ses_inner, "status", None)) else ses_inner.status
            listening_port = getattr(ss, "listening_port", None)
            if not listening_port and hasattr(ses_inner, "listen_port"):
                try:
                    lp = ses_inner.listen_port()
                    listening_port = int(lp) if lp else None
                except Exception:  # noqa: BLE001
                    listening_port = None
            peers = 0
            for h in handles_inner.values():
                try:
                    st = h.status() if callable(getattr(h, "status", None)) else h.status
                    peers += int(getattr(st, "num_peers", 0) or 0)
                except Exception:  # noqa: BLE001
                    continue
            return {
                "configured_port": configured,
                "listening_port": listening_port,
                "listening": bool(listening_port),
                "has_incoming": getattr(ss, "has_incoming_connections", None),
                "dht_nodes": getattr(ss, "dht_nodes", None),
                "peers": peers,
            }

        return await asyncio.to_thread(_collect)

    async def set_session_limits(
        self, download_limit: int | None, upload_limit: int | None
    ) -> dict[str, object]:
        async with self._lock:
            ses = self._ses
        if ses is None:
            return {}

        def _apply():
            if download_limit is not None and hasattr(ses, "set_download_rate_limit"):
                ses.set_download_rate_limit(max(0, int(download_limit)))
            if upload_limit is not None and hasattr(ses, "set_upload_rate_limit"):
                ses.set_upload_rate_limit(max(0, int(upload_limit)))

        await asyncio.to_thread(_apply)
        return await self.session_stats()

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

    async def remove(
        self,
        db_id: int,
        *,
        delete_files: bool = False,
        save_path: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        async with self._lock:
            ses = self._ses
            h = self._handles.pop(db_id, None) if ses is not None else None
            self._meta.pop(db_id, None)
        if ses is None:
            paths = self._guess_content_paths(save_path, display_name) if delete_files else []
            if delete_files and paths:
                await asyncio.to_thread(self._delete_paths_on_disk, paths, save_path)
            self._delete_persisted_torrent_file(db_id)
            delete_fastresume(db_id)
            return delete_files and bool(paths)
        paths: list[Path] = []
        had_handle = h is not None
        if h is not None:
            if delete_files:
                paths = await self._collect_paths_from_handle_async(h, save_path)
            lt = self._lt
            flags = self._libtorrent_delete_flags(lt, delete_files)

            def _rm():
                try:
                    if flags:
                        ses.remove_torrent(h, flags)
                    else:
                        ses.remove_torrent(h)
                except Exception as exc:  # noqa: BLE001
                    log.warning("remove_torrent db_id=%s: %s", db_id, exc)

            await asyncio.to_thread(_rm)
        elif delete_files:
            paths = self._guess_content_paths(save_path, display_name)

        if delete_files:
            if not paths:
                paths = self._guess_content_paths(save_path, display_name)
            await asyncio.to_thread(self._delete_paths_on_disk, paths, save_path)

        self._delete_persisted_torrent_file(db_id)
        delete_fastresume(db_id)
        return had_handle or (delete_files and bool(paths))


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
