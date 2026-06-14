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


async def fetch_all_runtime() -> dict[str, list[dict]]:
    """runtime rows keyed by engine_id."""
    out: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for spec in engine_specs():
            r = await client.get(f"{spec.url}/internal/v1/torrents")
            r.raise_for_status()
            rows = r.json()
            out[spec.id] = rows if isinstance(rows, list) else []
    return out


async def check_all_engines_health() -> dict:
    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for spec in engine_specs():
            r = await client.get(f"{spec.url}/health")
            r.raise_for_status()
            results[spec.id] = r.json()
    return results
