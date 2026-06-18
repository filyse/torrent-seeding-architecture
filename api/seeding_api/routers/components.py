"""Управление компонентами стека (перезапуск контейнеров) — только admin.

Перезапуск идёт через Docker Engine API (см. `seeding_api.docker_ctl`). Разрешён только
известный набор сервисов: ядро (api/db/redis/queue_worker) и движки (`engine-*`), чтобы
эндпоинт не превращался в произвольное управление любыми контейнерами хоста.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException

from seeding_api import docker_ctl
from seeding_api.auth import Principal, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/components", tags=["components"])

# Удерживаем ссылки на фоновые задачи самоперезапуска, чтобы их не собрал GC.
_bg_tasks: set[asyncio.Task] = set()

# Сервисы ядра, которые можно перезапускать из UI.
_CORE_SERVICES = {"api", "db", "redis", "queue_worker"}
_ENGINE_RE = re.compile(r"^engine-[a-z0-9][a-z0-9_-]{0,30}$")


def _is_allowed(service: str) -> bool:
    return service in _CORE_SERVICES or bool(_ENGINE_RE.match(service))


@router.get("")
async def list_components(_: Principal = Depends(require_admin)) -> dict:
    """Состояние контейнеров стека (для панели обслуживания)."""
    if not docker_ctl.docker_available():
        return {"available": False, "reason": "docker socket не смонтирован в контейнер api"}
    try:
        items = await docker_ctl.list_stack_containers()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    items = [c for c in items if c.get("service") and _is_allowed(c["service"])]
    items.sort(key=lambda c: c["service"])
    return {"available": True, "components": items}


@router.post("/{service}/restart")
async def restart_component(
    service: str, _: Principal = Depends(require_admin)
) -> dict:
    """Перезапустить контейнер сервиса. `api` рестартует с задержкой, чтобы успеть ответить."""
    if not _is_allowed(service):
        raise HTTPException(status_code=400, detail=f"сервис «{service}» нельзя перезапускать")
    if not docker_ctl.docker_available():
        raise HTTPException(
            status_code=503,
            detail="перезапуск недоступен: docker socket не смонтирован в контейнер api",
        )

    # Самоперезапуск: отвечаем сразу, рестарт — фоном через ~1.5 с, иначе ответ не дойдёт.
    if service == "api":
        async def _delayed() -> None:
            await asyncio.sleep(1.5)
            try:
                await docker_ctl.restart_service("api")
            except Exception as exc:  # noqa: BLE001
                log.error("delayed api restart failed: %s", exc)

        task = asyncio.create_task(_delayed())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return {"service": "api", "scheduled": True, "message": "API перезапускается…"}

    try:
        return await docker_ctl.restart_service(service)
    except LookupError:
        raise HTTPException(status_code=404, detail=f"контейнер сервиса «{service}» не найден")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"docker: {exc.response.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
