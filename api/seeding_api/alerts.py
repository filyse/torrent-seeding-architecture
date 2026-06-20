"""Встроенные алерты (Фаза 6): движок упал / раздачи в ошибке / диск кончается /
очередь зависла / БД недоступна.

Условия вычисляются по тем же агрегатам, что и health/metrics. Активные алерты отдаёт
`GET /api/v1/alerts` и показывает UI. Дополнительно фоновый цикл может слать уведомления
на вебхук (`SEEDING_ALERT_WEBHOOK`) при изменении состояния (новый/снятый алерт)."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from sqlalchemy import text

log = logging.getLogger(__name__)


# Порог в процентах ОТКЛЮЧЁН по умолчанию: на сидбоксе диски (18+ ТБ) намеренно
# забиты под завязку, поэтому «свободно 0.6%» — норма, а не повод для алерта.
def _disk_alert_pct() -> float:
    try:
        return max(0.0, float(os.getenv("SEEDING_DISK_ALERT_PCT", "0")))
    except ValueError:
        return 0.0


# Главный порог — абсолютный остаток в ГиБ. Меньше этого — критично (по умолчанию 100 ГиБ).
def _disk_alert_gb() -> float:
    try:
        return max(0.0, float(os.getenv("SEEDING_DISK_ALERT_GB", "100")))
    except ValueError:
        return 100.0


async def evaluate_alerts(app) -> list[dict]:
    alerts: list[dict] = []

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
    if not db_up:
        alerts.append({"id": "db_down", "severity": "critical", "title": "БД недоступна",
                       "message": "PostgreSQL не отвечает на запросы"})

    err = int(status_counts.get("error", 0) or 0)
    if err > 0:
        alerts.append({"id": "torrents_error", "severity": "warning",
                       "title": "Раздачи в ошибке",
                       "message": f"{err} раздач(и) в статусе error"})

    pool = getattr(app.state, "engine_pool", None)
    if pool is not None:
        pct = _disk_alert_pct()
        gb = _disk_alert_gb()
        stats = await pool.session_stats_all()
        for eid, st in stats.items():
            if st.get("error"):
                alerts.append({"id": f"engine_down:{eid}", "severity": "critical",
                               "title": f"Движок {eid} недоступен",
                               "message": f"Движок {eid} не отвечает на внутренний API"})
                continue
            total = int(st.get("disk_total") or 0)
            free = int(st.get("disk_free") or 0)
            if total > 0:
                free_pct = free / total * 100.0
                free_gb = free / (1024 ** 3)
                low = (gb > 0 and free_gb < gb) or (pct > 0 and free_pct < pct)
                if low:
                    alerts.append({
                        "id": f"disk_low:{eid}", "severity": "critical",
                        "title": f"Мало места на движке {eid}",
                        "message": f"Свободно {free_gb:.1f} ГиБ ({free_pct:.0f}%)",
                    })

    arq = getattr(app.state, "arq_pool", None)
    if arq is not None:
        interval = int(os.getenv("SEEDING_ARQ_HEALTH_INTERVAL", "30"))
        health_key = os.getenv("SEEDING_ARQ_HEALTH_KEY", "arq:queue:health-check")
        try:
            await arq.ping()
            raw = await arq.get(health_key)
            if raw is None:
                alerts.append({"id": "queue_stale", "severity": "warning",
                               "title": "Очередь молчит",
                               "message": f"Воркер не отчитывался дольше {interval} с"})
            else:
                pttl = await arq.pttl(health_key)
                if pttl and pttl > 0:
                    age = max(0, (interval + 1) - round(pttl / 1000))
                    if age > interval * 3:
                        alerts.append({"id": "queue_stale", "severity": "warning",
                                       "title": "Очередь отстаёт",
                                       "message": f"Отчёт воркера {age} с назад"})
        except Exception:  # noqa: BLE001
            alerts.append({"id": "redis_down", "severity": "warning",
                           "title": "Redis/очередь недоступны",
                           "message": "Не удалось связаться с Redis"})

    return alerts


def _webhook_url() -> str:
    return os.getenv("SEEDING_ALERT_WEBHOOK", "").strip()


async def _notify(url: str, text_msg: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"text": text_msg})
    except httpx.HTTPError as exc:
        log.warning("alert webhook failed: %s", exc)


async def alert_notifier_loop(app) -> None:
    """Периодически считает алерты; при изменении состава шлёт уведомления на вебхук."""
    url = _webhook_url()
    if not url:
        return
    try:
        interval = max(15, int(os.getenv("SEEDING_ALERT_INTERVAL", "60")))
    except ValueError:
        interval = 60
    known: dict[str, dict] = {}
    # Стартовая задержка, чтобы не алертить на ещё прогревающиеся движки.
    await asyncio.sleep(interval)
    try:
        while True:
            try:
                current = {a["id"]: a for a in await evaluate_alerts(app)}
                for aid, a in current.items():
                    if aid not in known:
                        await _notify(url, f"🔴 [{a['severity']}] {a['title']}: {a['message']}")
                for aid, a in known.items():
                    if aid not in current:
                        await _notify(url, f"✅ Снято: {a['title']}")
                known = current
                app.state.active_alerts = list(current.values())
            except Exception as exc:  # noqa: BLE001
                log.warning("alert evaluation failed: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
