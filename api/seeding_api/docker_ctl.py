"""Перезапуск контейнеров стека из контейнера `api` через Docker Engine API.

Общается с демоном по unix-сокету (`/var/run/docker.sock`, смонтирован в сервис `api`)
напрямую через httpx — без docker CLI/SDK. Контейнеры ищутся по compose-меткам
(`com.docker.compose.project` + `com.docker.compose.service`), поэтому имя compose-проекта
(`containerd` на CT400) нигде не хардкодится — оно берётся из меток самого `api`.
"""

from __future__ import annotations

import json
import logging
import os
import socket

import httpx

log = logging.getLogger(__name__)

_LABEL_PROJECT = "com.docker.compose.project"
_LABEL_SERVICE = "com.docker.compose.service"


def docker_socket() -> str:
    return os.getenv("SEEDING_DOCKER_SOCKET", "/var/run/docker.sock")


def docker_available() -> bool:
    return os.path.exists(docker_socket())


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=docker_socket())
    # base_url хост игнорируется при UDS, но нужен для относительных путей.
    return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=40.0)


async def _project_name(client: httpx.AsyncClient) -> str | None:
    """Имя compose-проекта: из env или из меток собственного контейнера (hostname=id)."""
    override = os.getenv("SEEDING_COMPOSE_PROJECT", "").strip()
    if override:
        return override
    host = socket.gethostname()
    try:
        r = await client.get(f"/containers/{host}/json")
        if r.status_code == 200:
            labels = (r.json().get("Config", {}) or {}).get("Labels", {}) or {}
            proj = labels.get(_LABEL_PROJECT)
            if proj:
                return str(proj)
    except Exception as exc:  # noqa: BLE001
        log.warning("docker: project autodetect failed: %s", exc)
    return None


def _filters(project: str | None, service: str | None) -> str:
    labels: list[str] = []
    if project:
        labels.append(f"{_LABEL_PROJECT}={project}")
    if service:
        labels.append(f"{_LABEL_SERVICE}={service}")
    return json.dumps({"label": labels})


def _short_name(item: dict) -> str:
    names = item.get("Names") or []
    return names[0].lstrip("/") if names else item.get("Id", "?")[:12]


async def list_stack_containers() -> list[dict]:
    """Контейнеры compose-проекта `api` с базовым состоянием (для панели обслуживания)."""
    if not docker_available():
        return []
    async with _client() as client:
        project = await _project_name(client)
        r = await client.get(
            "/containers/json",
            params={"all": "1", "filters": _filters(project, None)},
        )
        r.raise_for_status()
        out: list[dict] = []
        for c in r.json():
            labels = c.get("Labels", {}) or {}
            out.append(
                {
                    "service": labels.get(_LABEL_SERVICE),
                    "container": _short_name(c),
                    "state": c.get("State"),  # running | exited | ...
                    "status": c.get("Status"),  # "Up 2 hours (healthy)"
                }
            )
        return out


async def restart_service(service: str, *, timeout_s: int = 10) -> dict:
    """Перезапустить контейнер сервиса `service` в текущем compose-проекте.

    Бросает LookupError, если контейнер не найден; httpx.HTTPStatusError при ошибке демона.
    """
    if not docker_available():
        raise RuntimeError("docker socket недоступен (не смонтирован /var/run/docker.sock)")
    async with _client() as client:
        project = await _project_name(client)
        r = await client.get(
            "/containers/json",
            params={"all": "1", "filters": _filters(project, service)},
        )
        r.raise_for_status()
        items = r.json()
        if not items:
            raise LookupError(service)
        cid = items[0]["Id"]
        cname = _short_name(items[0])
        resp = await client.post(f"/containers/{cid}/restart", params={"t": str(timeout_s)})
        resp.raise_for_status()
        log.info("docker: restarted service=%s container=%s", service, cname)
        return {"service": service, "container": cname, "restarted": True}
