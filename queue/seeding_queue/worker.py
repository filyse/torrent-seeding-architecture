import logging
import os
from urllib.parse import urlparse

import httpx
from arq.connections import RedisSettings

log = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    u = urlparse(url)
    host = u.hostname or "localhost"
    port = u.port or 6379
    password = u.password
    return RedisSettings(host=host, port=port, password=password)


async def noop_report(ctx):
    """Заглушка: фоновая задача для проверки воркера."""
    return {"ok": True}


async def check_engine_health(ctx):
    """Проверка доступности движка (осмысленная задача вместо только noop)."""
    url = os.getenv("ENGINE_URL", "http://127.0.0.1:8081").rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{url}/health")
        r.raise_for_status()
        body = r.json()
    log.info("engine health job ok backend=%s", body.get("backend"))
    return {"ok": True, "engine": body}


class WorkerSettings:
    functions = [noop_report, check_engine_health]
    redis_settings = _redis_settings()
    max_jobs = 4
