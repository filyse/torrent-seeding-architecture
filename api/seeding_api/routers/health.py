"""Сводный health-check всех компонентов платформы для страницы настроек.

Возвращает статус ядра (API, БД, Redis, очередь ARQ) и каждого движка с задержками
и краткими деталями. Используется UI («Состояние сервисов») и пригоден для мониторинга.
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from sqlalchemy import text

from seeding_api.engine_pool import EnginePool

router = APIRouter(tags=["health"])


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def _check_database(app) -> dict:
    comp = {"id": "database", "name": "PostgreSQL", "kind": "core", "status": "down"}
    t0 = time.perf_counter()
    try:
        factory = app.state.session_factory
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        comp.update(status="ok", latency_ms=_ms(t0), detail="Подключение в норме")
    except Exception as exc:  # noqa: BLE001
        comp.update(detail=f"Ошибка: {exc}")
    return comp


async def _check_redis_and_queue(app) -> list[dict]:
    redis = {"id": "redis", "name": "Redis", "kind": "core", "status": "down"}
    queue = {"id": "queue", "name": "Очередь (ARQ)", "kind": "core", "status": "down"}
    arq = getattr(app.state, "arq_pool", None)
    if arq is None:
        redis.update(status="warn", detail="REDIS_URL не задан")
        queue.update(status="warn", detail="Очередь не настроена")
        return [redis, queue]

    t0 = time.perf_counter()
    try:
        await arq.ping()
        redis.update(status="ok", latency_ms=_ms(t0), detail="PING ok")
    except Exception as exc:  # noqa: BLE001
        redis.update(detail=f"Ошибка: {exc}")
        queue.update(status="warn", detail="Redis недоступен")
        return [redis, queue]

    health_key = os.getenv("SEEDING_ARQ_HEALTH_KEY", "arq:queue:health-check")
    interval = int(os.getenv("SEEDING_ARQ_HEALTH_INTERVAL", "30"))
    try:
        raw = await arq.get(health_key)
        if raw is None:
            queue.update(
                status="warn",
                detail=f"Воркер не отчитывался дольше {interval} с (остановлен?)",
            )
            return [redis, queue]
        val = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        # Возраст отчёта по TTL ключа: arq ставит psetex на (interval + 1) с.
        age = None
        try:
            pttl = await arq.pttl(health_key)
            if pttl and pttl > 0:
                age = max(0, (interval + 1) - round(pttl / 1000))
        except Exception:  # noqa: BLE001
            pass
        bits = []
        if age is not None:
            bits.append(f"отчёт {age} с назад")
        # Из строки arq вытащим счётчики задач для краткой сводки.
        for token in val.split():
            if token.startswith(("j_complete=", "j_failed=", "queued=", "j_ongoing=")):
                bits.append(token)
        stale = age is not None and age > interval * 3
        queue.update(
            status="warn" if stale else "ok",
            detail=" · ".join(bits) if bits else val.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        queue.update(status="warn", detail=f"Не удалось прочитать статус воркера: {exc}")
    return [redis, queue]


async def _check_engine(pool: EnginePool, spec) -> dict:
    comp = {
        "id": f"engine:{spec.id}",
        "name": f"Движок {spec.id}",
        "kind": "engine",
        "engine_id": spec.id,
        "url": spec.url,
        "tls": spec.url.startswith("https://"),
        "status": "down",
    }
    try:
        client = pool.client_for(spec.id)
    except KeyError:
        comp.update(status="warn", detail="Не в активном пуле (stale?)")
        return comp
    t0 = time.perf_counter()
    try:
        hc = await client.health()
        comp.update(status="ok", latency_ms=_ms(t0))
        if isinstance(hc, dict):
            if hc.get("version"):
                comp["version"] = str(hc["version"])
            if hc.get("built_at"):
                comp["built_at"] = str(hc["built_at"])
    except httpx.HTTPError as exc:
        comp.update(detail=f"Недоступен: {exc}")
        return comp
    try:
        bt = await client.net_status()
        comp["meta"] = bt
        port = spec.listen_port or bt.get("configured_port")
        parts = []
        if port:
            parts.append(f"BT-порт {port}")
        if bt.get("listening") is False:
            comp["status"] = "warn"
            parts.append("не слушает")
        if bt.get("dht_nodes") is not None:
            parts.append(f"DHT {bt.get('dht_nodes')}")
        if bt.get("has_incoming"):
            parts.append("входящие ✓")
        comp["detail"] = " · ".join(parts) if parts else "Онлайн"
    except httpx.HTTPError:
        comp["detail"] = "Онлайн (сетевой статус недоступен)"
    return comp


async def build_health_full(app) -> dict:
    """Сводный health всех компонентов. Вынесено из роута, чтобы переиспользовать в WS-пуллере
    (канал ``engines``)."""
    pool: EnginePool = app.state.engine_pool
    from seeding_api.buildinfo import version_info

    api_comp = {
        "id": "api",
        "name": "API",
        "kind": "core",
        "status": "ok",
        "detail": "Отвечает",
        **version_info(),
    }
    db_comp, redisqueue, engine_comps = await asyncio.gather(
        _check_database(app),
        _check_redis_and_queue(app),
        asyncio.gather(*[_check_engine(pool, s) for s in pool.specs]),
    )
    components = [api_comp, db_comp, *redisqueue, *engine_comps]

    core = [c for c in components if c["kind"] == "core"]
    engines = [c for c in components if c["kind"] == "engine"]
    if any(c["status"] == "down" for c in core):
        overall = "down"
    elif any(c["status"] in ("down", "warn") for c in components):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "engines_ok": sum(1 for c in engines if c["status"] == "ok"),
            "engines_total": len(engines),
        },
        "components": components,
    }


@router.get("/health/full")
async def health_full(request: Request):
    return await build_health_full(request.app)


@router.get("/alerts")
async def alerts(request: Request):
    from seeding_api.alerts import evaluate_alerts

    items = await evaluate_alerts(request.app)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "critical": sum(1 for a in items if a.get("severity") == "critical"),
        "alerts": items,
    }


@router.get("/system")
async def system(request: Request):
    from seeding_api.system_stats import collect_system_stats

    data = await collect_system_stats()
    return {"generated_at": datetime.now(timezone.utc).isoformat(), **data}
