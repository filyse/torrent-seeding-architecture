"""Утилиты воркера для multi-engine.

Движки резолвятся из статической конфигурации (`ENGINES_CONFIG`) **плюс** динамического
реестра в БД (самозарегистрированные движки, Фаза 4.5) — так же, как пул API. Без БД-части
cron/джобы воркера не видели переехавшие на отдельные хосты движки (`ENGINES_CONFIG` пуст),
и кнопки «Проверить здоровье / Восстановить все / Сверить runtime» отвечали «движки не ответили».
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from seeding_db.config import get_database_url
from seeding_db.engine_registry import EngineSpec, load_engine_specs
from seeding_db.repository import EngineRepository
from seeding_db.session import create_engine, create_session_factory


def _ttl_seconds() -> int:
    try:
        return max(30, int(os.getenv("SEEDING_ENGINE_TTL", "180")))
    except ValueError:
        return 180


def _merge_specs(static: list[EngineSpec], db_rows) -> list[EngineSpec]:
    """Слить статику и БД по id (поля БД переопределяют статику, но None из БД не затирают)."""
    merged: dict[str, EngineSpec] = {s.id: s for s in static}
    for r in db_rows:
        base = merged.get(r.id)
        merged[r.id] = EngineSpec(
            id=r.id,
            url=(r.url or (base.url if base else "")),
            storage_prefix=(r.storage_prefix or (base.storage_prefix if base else "")),
            listen_port=(r.listen_port if r.listen_port is not None else (base.listen_port if base else None)),
            media_path=(r.media_path or (base.media_path if base else None)),
        )
    return list(merged.values())


def _evict_stale(static_ids: set[str], db_rows) -> list:
    """Убрать самозарегистрированные движки без свежего heartbeat (статические оставляем всегда)."""
    ttl = _ttl_seconds()
    now = datetime.now(timezone.utc)
    kept = []
    for r in db_rows:
        if r.id in static_ids:
            kept.append(r)
            continue
        ls = r.last_seen
        if ls is None:
            continue
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)
        if (now - ls).total_seconds() <= ttl:
            kept.append(r)
    return kept


async def resolve_specs(session=None) -> list[EngineSpec]:
    """env-спеки + живой динамический реестр из БД. `session` переиспользуется, если передан."""
    static = load_engine_specs()
    rows = []
    if session is not None:
        try:
            rows = await EngineRepository(session).list_enabled()
        except Exception:  # noqa: BLE001
            rows = []
    else:
        eng = create_engine(get_database_url())
        sf = create_session_factory(eng)
        try:
            async with sf() as s:
                rows = await EngineRepository(s).list_enabled()
        except Exception:  # noqa: BLE001
            rows = []
        finally:
            await eng.dispose()
    return _merge_specs(static, _evict_stale({sp.id for sp in static}, rows))


def engine_specs() -> list[EngineSpec]:
    """Только статические спеки (env). Для динамических используйте `resolve_specs()`."""
    return load_engine_specs()


async def engine_url(engine_id: str, session=None) -> str:
    for spec in await resolve_specs(session):
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
    """runtime rows keyed by engine_id. Недоступный движок пропускаем, не валя весь проход."""
    out: dict[str, list[dict]] = {}
    for spec in await resolve_specs():
        try:
            async with make_engine_client(spec.url, 30.0) as client:
                r = await client.get(f"{spec.url}/internal/v1/torrents")
                r.raise_for_status()
                rows = r.json()
                out[spec.id] = rows if isinstance(rows, list) else []
        except httpx.HTTPError:
            continue
    return out


async def check_all_engines_health() -> dict:
    results: dict[str, dict] = {}
    for spec in await resolve_specs():
        try:
            async with make_engine_client(spec.url, 15.0) as client:
                r = await client.get(f"{spec.url}/health")
                r.raise_for_status()
                results[spec.id] = r.json()
        except httpx.HTTPError as exc:
            results[spec.id] = {"error": str(exc)}
    return results
