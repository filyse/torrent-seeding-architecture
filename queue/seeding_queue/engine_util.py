"""Утилиты воркера для multi-engine."""

from __future__ import annotations

import os

import httpx
from seeding_db.engine_registry import EngineSpec, load_engine_specs


def engine_specs() -> list[EngineSpec]:
    return load_engine_specs()


def engine_url(engine_id: str) -> str:
    for spec in engine_specs():
        if spec.id == engine_id:
            return spec.url
    raise KeyError(f"unknown engine_id: {engine_id}")


def _api_token() -> str:
    return os.getenv("SEEDING_ENGINE_API_TOKEN", "").strip()


def _verify_for(base_url: str):
    """TLS-проверка движка: для https с приватным CA — по SEEDING_ENGINE_TLS_CA, иначе системные корни."""
    if not base_url.startswith("https://"):
        return True
    ca = os.getenv("SEEDING_ENGINE_TLS_CA", "").strip()
    return ca if ca else True


def make_engine_client(base_url: str, timeout: float = 30.0) -> httpx.AsyncClient:
    """httpx-клиент к движку с тем же TLS(CA)+токеном, что и у оркестратора.

    Без этого cron-задачи воркера падали на TLS-движках (CERTIFICATE_VERIFY_FAILED /
    401), отсюда копился j_failed."""
    token = _api_token()
    headers = {"X-Engine-Token": token} if token else None
    transport = httpx.AsyncHTTPTransport(verify=_verify_for(base_url))
    return httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)


async def fetch_all_runtime() -> dict[str, list[dict]]:
    """runtime rows keyed by engine_id."""
    out: dict[str, list[dict]] = {}
    for spec in engine_specs():
        async with make_engine_client(spec.url, 30.0) as client:
            r = await client.get(f"{spec.url}/internal/v1/torrents")
            r.raise_for_status()
            rows = r.json()
            out[spec.id] = rows if isinstance(rows, list) else []
    return out


async def check_all_engines_health() -> dict:
    results: dict[str, dict] = {}
    for spec in engine_specs():
        async with make_engine_client(spec.url, 15.0) as client:
            r = await client.get(f"{spec.url}/health")
            r.raise_for_status()
            results[spec.id] = r.json()
    return results
