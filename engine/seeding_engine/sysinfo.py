"""Сбор информации о хосте/рантайме движка для детальной карточки в UI.

Только stdlib (без psutil) — читаем /proc, socket, shutil. Внешний (WAN) IP
получаем best-effort и кэшируем, чтобы не дёргать внешний сервис на каждый запрос.
"""
import os
import platform
import shutil
import socket
import time
import urllib.request

_START = time.monotonic()
_WAN_CACHE: dict[str, object] = {"ip": None, "ts": 0.0}
_WAN_TTL = 300.0
_WAN_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")


def _primary_ip() -> str | None:
    """Локальный (LAN) IP, с которого движок ходит наружу."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


def _wan_ip() -> str | None:
    now = time.monotonic()
    if _WAN_CACHE["ip"] and now - float(_WAN_CACHE["ts"]) < _WAN_TTL:
        return _WAN_CACHE["ip"]  # type: ignore[return-value]
    for url in _WAN_SERVICES:
        try:
            ip = urllib.request.urlopen(url, timeout=4).read().decode().strip()
            if ip and len(ip) <= 45:
                _WAN_CACHE["ip"] = ip
                _WAN_CACHE["ts"] = now
                return ip
        except Exception:  # noqa: BLE001
            continue
    return _WAN_CACHE["ip"]  # type: ignore[return-value]


def _cpu_pct() -> float | None:
    def _read() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="ascii") as f:
                vals = [int(x) for x in f.readline().split()[1:]]
        except Exception:  # noqa: BLE001
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)

    a = _read()
    if a is None:
        return None
    time.sleep(0.15)
    b = _read()
    if b is None:
        return None
    dt = b[1] - a[1]
    if dt <= 0:
        return None
    return round(100.0 * (1.0 - (b[0] - a[0]) / dt), 1)


def _meminfo() -> tuple[int | None, int | None]:
    total = avail = None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key == "MemTotal":
                    total = int(rest.split()[0]) * 1024
                elif key == "MemAvailable":
                    avail = int(rest.split()[0]) * 1024
                if total is not None and avail is not None:
                    break
    except Exception:  # noqa: BLE001
        pass
    return total, avail


def _proc_rss() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001
        pass
    return None


def _lt_version() -> str | None:
    try:
        import libtorrent as lt  # noqa: PLC0415

        return str(getattr(lt, "version", None) or getattr(lt, "__version__", None))
    except Exception:  # noqa: BLE001
        return None


def _disk(path: str) -> tuple[int | None, int | None]:
    try:
        u = shutil.disk_usage(path)
        return u.total, u.free
    except Exception:  # noqa: BLE001
        return None, None


def _loadavg() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except Exception:  # noqa: BLE001
        return None


def collect(rt=None) -> dict:
    """Собрать sysinfo. Тяжёлые вызовы (cpu sample, wan) — синхронно, вызывать в to_thread."""
    data_root = os.getenv("SEEDING_DATA_ROOT", "/data")
    mem_total, mem_avail = _meminfo()
    disk_total, disk_free = _disk(data_root)
    load = _loadavg()
    out: dict = {
        "engine_id": os.getenv("SEEDING_ENGINE_ID", "") or os.getenv("ENGINE_STORAGE_SUBDIR", ""),
        "hostname": socket.gethostname(),
        "backend": getattr(rt, "backend_name", None),
        "os": platform.platform(),
        "python": platform.python_version(),
        "libtorrent": _lt_version(),
        "uptime_seconds": int(time.monotonic() - _START),
        "data_root": data_root,
        "local_ip": _primary_ip(),
        "wan_ip": _wan_ip(),
        "advertise_url": os.getenv("SEEDING_ENGINE_ADVERTISE_URL", ""),
        "listen_interfaces": os.getenv("LT_LISTEN_INTERFACES", ""),
        "cpu_count": os.cpu_count(),
        "cpu_pct": _cpu_pct(),
        "load1": round(load[0], 2) if load else None,
        "load5": round(load[1], 2) if load else None,
        "load15": round(load[2], 2) if load else None,
        "mem_total": mem_total,
        "mem_available": mem_avail,
        "proc_rss": _proc_rss(),
        "disk_total": disk_total,
        "disk_free": disk_free,
    }
    return out
