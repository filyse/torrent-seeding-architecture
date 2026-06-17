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


async def _check_database(request: Request) -> dict:
    comp = {"id": "database", "name": "PostgreSQL", "kind": "core", "status": "down"}
    t0 = time.perf_counter()
    try:
        factory = request.app.state.session_factory
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        comp.update(status="ok", latency_ms=_ms(t0), detail="Подключение в норме")
    except Exception as exc:  # noqa: BLE001
        comp.update(detail=f"Ошибка: {exc}")
    return comp


async def _check_redis_and_queue(request: Request) -> list[dict]:
    redis = {"id": "redis", "name": "Redis", "kind": "core", "status": "down"}
    queue = {"id": "queue", "name": "Очередь (ARQ)", "kind": "core", "status": "down"}
    arq = getattr(request.app.state, "arq_pool", None)
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
    try:
        raw = await arq.get(health_key)
        if raw is None:
            queue.update(status="warn", detail="Воркер ещё не отчитывался")
        else:
            val = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            queue.update(status="ok", detail=val.strip())
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
        await client.health()
        comp.update(status="ok", latency_ms=_ms(t0))
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


@router.get("/health/full")
async def health_full(request: Request):
    pool: EnginePool = request.app.state.engine_pool
    api_comp = {
        "id": "api",
        "name": "API",
        "kind": "core",
        "status": "ok",
        "detail": "Отвечает",
    }
    db_comp, redisqueue, engine_comps = await asyncio.gather(
        _check_database(request),
        _check_redis_and_queue(request),
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
