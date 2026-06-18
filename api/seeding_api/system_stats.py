"""Системные метрики (CPU/RAM/диск/I-O) для панели «Нагрузка системы» в настройках.

Источник — Prometheus (ряды node-exporter по хосту и cAdvisor по контейнерам). API
выполняет набор мгновенных PromQL-запросов и собирает компактный снимок. Если стек
наблюдаемости не поднят/недоступен — возвращаем `available: false` без ошибки."""

from __future__ import annotations

import asyncio
import os

import httpx


def _prom_url() -> str:
    return os.getenv("SEEDING_PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")


async def _query(client: httpx.AsyncClient, expr: str) -> list[tuple[dict, float]]:
    """Мгновенный запрос; вернуть список (labels, value)."""
    r = await client.get("/api/v1/query", params={"query": expr})
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        return []
    res = data["data"]
    rtype = res.get("resultType")
    out: list[tuple[dict, float]] = []
    if rtype == "scalar":
        try:
            out.append(({}, float(res["result"][1])))
        except (KeyError, IndexError, ValueError):
            pass
        return out
    for item in res.get("result", []):
        try:
            out.append((item.get("metric", {}), float(item["value"][1])))
        except (KeyError, IndexError, ValueError):
            continue
    return out


def _scalar(rows: list[tuple[dict, float]]) -> float | None:
    return rows[0][1] if rows else None


def _short_name(full: str) -> str:
    """containerd-engine-b1-1 -> engine-b1; containerd-api-1 -> api."""
    n = full
    if n.startswith("containerd-"):
        n = n[len("containerd-"):]
    if n.endswith("-1"):
        n = n[: -len("-1")]
    return n


async def collect_system_stats() -> dict:
    base = _prom_url()
    queries = {
        "cpu_pct": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        "load1": "node_load1",
        "load5": "node_load5",
        "load15": "node_load15",
        "cpu_cores": "count(count by (cpu) (node_cpu_seconds_total))",
        "mem_total": "node_memory_MemTotal_bytes",
        "mem_avail": "node_memory_MemAvailable_bytes",
        # В LXC node_exporter недостоверно отдаёт MemAvailable (lxcfs), поэтому реальное
        # потребление берём из memory-cgroup корня (cAdvisor) — это и есть память всего CT.
        # rss (анонимная память процессов) — реальная нагрузка приложений. working_set и
        # usage включают page cache от torrent-I/O (десятки сотен МБ, вытесняемые ядром),
        # из-за чего RAM в LXC выглядел завышенным (~72%). Берём rss, иначе фоллбэк.
        "cg_mem_rss": 'container_memory_rss{id="/"}',
        "cg_mem_used": 'container_memory_working_set_bytes{id="/"}',
        "cg_mem_usage": 'container_memory_usage_bytes{id="/"}',
        "cg_mem_limit": 'container_spec_memory_limit_bytes{id="/"}',
        "machine_mem": "machine_memory_bytes",
        "disk_read_bps": "sum(rate(node_disk_read_bytes_total[1m]))",
        "disk_write_bps": "sum(rate(node_disk_written_bytes_total[1m]))",
        "fs_size": 'node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"}',
        "fs_avail": 'node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"}',
        "c_cpu": 'sum by (name) (rate(container_cpu_usage_seconds_total{name=~"containerd-.*"}[1m]))',
        "c_mem": 'sum by (name) (container_memory_usage_bytes{name=~"containerd-.*"})',
        "c_ior": 'sum by (name) (rate(container_fs_reads_bytes_total{name=~"containerd-.*"}[1m]))',
        "c_iow": 'sum by (name) (rate(container_fs_writes_bytes_total{name=~"containerd-.*"}[1m]))',
    }
    try:
        async with httpx.AsyncClient(base_url=base, timeout=8.0) as client:
            results = await asyncio.gather(
                *[_query(client, expr) for expr in queries.values()],
                return_exceptions=True,
            )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    res: dict[str, list[tuple[dict, float]]] = {}
    any_ok = False
    for key, val in zip(queries.keys(), results):
        if isinstance(val, Exception):
            res[key] = []
        else:
            res[key] = val
            if val:
                any_ok = True
    if not any_ok:
        return {"available": False, "reason": "Prometheus вернул пустой ответ (экспортёры ещё не собрали данные?)"}

    # Память: предпочитаем cgroup-данные (корректны в LXC), иначе падаем на node-exporter.
    node_total = _scalar(res["mem_total"])
    node_avail = _scalar(res["mem_avail"])
    machine_mem = _scalar(res["machine_mem"])
    cg_used = (
        _scalar(res["cg_mem_rss"])
        or _scalar(res["cg_mem_used"])
        or _scalar(res["cg_mem_usage"])
    )
    cg_limit = _scalar(res["cg_mem_limit"])
    # Лимит cgroup валиден, если он положительный и не «безлимит» (≈ вся физ. память хоста).
    limit_ok = (
        cg_limit is not None
        and cg_limit > 0
        and (machine_mem is None or cg_limit < machine_mem * 0.95)
    )
    mem_total = cg_limit if limit_ok else node_total
    if cg_used is not None:
        mem_used = cg_used
    elif node_total is not None and node_avail is not None:
        mem_used = node_total - node_avail
    else:
        mem_used = None

    # Файловые системы: сопоставить size/avail по mountpoint.
    fs_size = {m.get("mountpoint", "?"): v for m, v in res["fs_size"]}
    fs_avail = {m.get("mountpoint", "?"): v for m, v in res["fs_avail"]}
    filesystems = []
    for mount, size in sorted(fs_size.items()):
        avail = fs_avail.get(mount)
        if not size or avail is None:
            continue
        used = size - avail
        filesystems.append({
            "mount": mount,
            "total": int(size),
            "used": int(used),
            "free": int(avail),
            "pct": round(used / size * 100, 1) if size else None,
        })

    # Контейнеры: объединить cpu/mem/io по имени.
    containers: dict[str, dict] = {}
    for m, v in res["c_cpu"]:
        nm = m.get("name")
        if nm:
            containers.setdefault(nm, {})["cpu_pct"] = round(v * 100, 1)
    for m, v in res["c_mem"]:
        nm = m.get("name")
        if nm:
            containers.setdefault(nm, {})["mem_bytes"] = int(v)
    for m, v in res["c_ior"]:
        nm = m.get("name")
        if nm:
            containers.setdefault(nm, {})["io_read_bps"] = int(v)
    for m, v in res["c_iow"]:
        nm = m.get("name")
        if nm:
            containers.setdefault(nm, {})["io_write_bps"] = int(v)
    container_list = [
        {"name": _short_name(nm), "full": nm, **vals}
        for nm, vals in sorted(containers.items(), key=lambda kv: kv[0])
    ]

    cpu_pct = _scalar(res["cpu_pct"])
    return {
        "available": True,
        "host": {
            "cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else None,
            "cpu_cores": int(_scalar(res["cpu_cores"])) if _scalar(res["cpu_cores"]) else None,
            "load1": _scalar(res["load1"]),
            "load5": _scalar(res["load5"]),
            "load15": _scalar(res["load15"]),
            "mem_total": int(mem_total) if mem_total is not None else None,
            "mem_used": int(mem_used) if mem_used is not None else None,
            "mem_pct": round(mem_used / mem_total * 100, 1) if (mem_used is not None and mem_total) else None,
            "disk_read_bps": int(_scalar(res["disk_read_bps"]) or 0),
            "disk_write_bps": int(_scalar(res["disk_write_bps"]) or 0),
            "filesystems": filesystems,
        },
        "containers": container_list,
    }
