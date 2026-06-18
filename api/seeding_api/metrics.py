"""Prometheus-метрики платформы (Фаза 6).

Текстовый формат экспозиции собирается вручную (без внешних зависимостей). Источник
данных — те же агрегаты, что и у дашборда: счётчики раздач из БД, посессионная
статистика движков, отчёт ARQ-воркера и тайминг restore при старте API."""

from __future__ import annotations

import os
import time

from sqlalchemy import text

from seeding_api.engine_pool import EnginePool


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class _Doc:
    def __init__(self) -> None:
        self._lines: list[str] = []
        self._declared: set[str] = set()

    def metric(self, name: str, value, labels: dict | None = None, *, help: str = "", typ: str = "gauge") -> None:
        if value is None:
            return
        if name not in self._declared:
            if help:
                self._lines.append(f"# HELP {name} {help}")
            self._lines.append(f"# TYPE {name} {typ}")
            self._declared.add(name)
        if labels:
            lbl = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            self._lines.append(f"{name}{{{lbl}}} {_fmt(value)}")
        else:
            self._lines.append(f"{name} {_fmt(value)}")

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    return str(int(value))


def _parse_arq_jobs(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    mapping = {
        "j_complete=": "complete",
        "j_failed=": "failed",
        "j_ongoing=": "ongoing",
        "queued=": "queued",
        "j_retried=": "retried",
    }
    for token in raw.split():
        for prefix, label in mapping.items():
            if token.startswith(prefix):
                try:
                    out[label] = int(token[len(prefix):])
                except ValueError:
                    pass
    return out


async def render_metrics(app) -> str:
    doc = _Doc()
    doc.metric(
        "seeding_build_info", 1, {"version": getattr(app, "version", "0")},
        help="Информация о сборке API", typ="gauge",
    )

    # --- БД и счётчики раздач ---
    factory = getattr(app.state, "session_factory", None)
    db_up = False
    status_counts: dict[str, int] = {}
    if factory is not None:
        try:
            async with factory() as s:
                await s.execute(text("SELECT 1"))
                db_up = True
                from seeding_db.repository import TorrentRepository

                status_counts = await TorrentRepository(s).count_by_status()
        except Exception:  # noqa: BLE001
            db_up = False
    doc.metric("seeding_database_up", db_up, help="Доступность PostgreSQL (1/0)")
    total_torrents = 0
    for status, count in status_counts.items():
        doc.metric(
            "seeding_torrents", count, {"status": status},
            help="Число раздач по статусу (логических, из БД)", typ="gauge",
        )
        total_torrents += count
    doc.metric("seeding_torrents_total_count", total_torrents, help="Всего логических раздач в БД")

    # --- Движки (посессионная статистика) ---
    pool: EnginePool | None = getattr(app.state, "engine_pool", None)
    if pool is not None:
        stats = await pool.session_stats_all()
        for eid, st in stats.items():
            up = not st.get("error")
            doc.metric("seeding_engine_up", up, {"engine": eid}, help="Движок доступен (1/0)")
            if not up:
                continue
            gauges = {
                "seeding_engine_torrents": st.get("torrents"),
                "seeding_engine_torrents_active": st.get("torrents_active"),
                "seeding_engine_download_rate_bytes": st.get("download_rate"),
                "seeding_engine_upload_rate_bytes": st.get("upload_rate"),
                "seeding_engine_disk_total_bytes": st.get("disk_total"),
                "seeding_engine_disk_free_bytes": st.get("disk_free"),
                "seeding_engine_peers": st.get("peers"),
                "seeding_engine_seeds": st.get("seeds"),
                "seeding_engine_dht_nodes": st.get("dht_nodes"),
                "seeding_engine_torrent_errors": st.get("errors"),
            }
            for name, val in gauges.items():
                doc.metric(name, val, {"engine": eid}, help="Метрика движка", typ="gauge")
            counters = {
                "seeding_engine_uploaded_bytes_total": st.get("total_uploaded"),
                "seeding_engine_downloaded_bytes_total": st.get("total_downloaded"),
            }
            for name, val in counters.items():
                doc.metric(name, val, {"engine": eid}, help="Накопленный объём движка", typ="counter")

    # --- Очередь ARQ ---
    arq = getattr(app.state, "arq_pool", None)
    queue_up = False
    if arq is not None:
        try:
            await arq.ping()
            queue_up = True
        except Exception:  # noqa: BLE001
            queue_up = False
        if queue_up:
            health_key = os.getenv("SEEDING_ARQ_HEALTH_KEY", "arq:queue:health-check")
            interval = int(os.getenv("SEEDING_ARQ_HEALTH_INTERVAL", "30"))
            try:
                raw = await arq.get(health_key)
                if raw is not None:
                    val = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    age = None
                    try:
                        pttl = await arq.pttl(health_key)
                        if pttl and pttl > 0:
                            age = max(0, (interval + 1) - round(pttl / 1000))
                    except Exception:  # noqa: BLE001
                        pass
                    if age is not None:
                        doc.metric("seeding_queue_report_age_seconds", age, help="Возраст отчёта воркера")
                    for label, n in _parse_arq_jobs(val).items():
                        doc.metric(
                            "seeding_queue_jobs", n, {"state": label},
                            help="Счётчики задач ARQ", typ="gauge",
                        )
            except Exception:  # noqa: BLE001
                pass
    doc.metric("seeding_queue_up", queue_up, help="Очередь ARQ доступна (1/0)")

    # --- Restore при старте API ---
    rs = getattr(app.state, "restore_stats", None)
    if isinstance(rs, dict):
        doc.metric(
            "seeding_restore_duration_seconds", rs.get("duration"),
            help="Длительность восстановления раздач при старте API",
        )
        doc.metric("seeding_restore_torrents_total", rs.get("count"), help="Сколько раздач восстановлено")
        if rs.get("finished_at"):
            doc.metric(
                "seeding_restore_age_seconds", max(0, int(time.time() - rs["finished_at"])),
                help="Сколько секунд назад завершился restore",
            )

    return doc.text()
