"""Пул HTTP-клиентов к нескольким движкам.

Источник специй — статический `engines.json` (база) плюс динамический реестр в БД
(Фаза 4.5): движки могут регистрироваться сами по API-ключу. `refresh()` сливает оба
источника (записи БД дополняют/переопределяют статику, но не затирают известные поля
вроде `media_path` значениями None) и пересобирает HTTP-клиентов под изменившийся состав.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from seeding_db.engine_registry import (
    EngineSpec,
    load_engine_specs,
    match_engine_id,
    resolve_engine_id,
    spec_by_id,
)
from seeding_db.repository import EngineRepository

from seeding_api.engine_client import EngineClient

log = logging.getLogger(__name__)


def engine_ttl_seconds() -> int:
    """Сколько секунд без heartbeat движок ещё считается живым (по умолчанию 3 пропуска)."""
    try:
        return max(30, int(os.getenv("SEEDING_ENGINE_TTL", "180")))
    except ValueError:
        return 180


def stats_deadline_seconds() -> float:
    """Сколько ждать `session/stats` одного движка в «живых» путях (UI, SSE, WS).

    Один медленный/недоступный движок (потеря SYN, висящий коннект) раньше держал весь
    ответ: опрос шёл последовательно, а у клиента таймаут 30 с плюс ретраи коннекта."""
    try:
        return max(0.5, float(os.getenv("SEEDING_ENGINE_STATS_DEADLINE", "5")))
    except ValueError:
        return 5.0


def stats_stale_max_seconds() -> float:
    """Сколько секунд последний удачный ответ движка можно отдавать с пометкой `stale`."""
    try:
        return max(0.0, float(os.getenv("SEEDING_ENGINE_STATS_STALE_MAX", "120")))
    except ValueError:
        return 120.0


def _merge_specs(static: list[EngineSpec], db_rows) -> list[EngineSpec]:
    """Слить статику и БД по id. Поля из БД переопределяют статику, но пустые/None из БД
    не затирают известные значения статики (важно для `media_path`)."""
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


class EnginePool:
    def __init__(self, specs: list[EngineSpec] | None = None, session_factory=None) -> None:
        self._static = specs if specs is not None else load_engine_specs()
        self._session_factory = session_factory
        self._specs = list(self._static)
        self._by_id: dict[str, EngineClient] = {s.id: EngineClient(s.url) for s in self._specs}
        self._lock = asyncio.Lock()
        # Последний удачный `session/stats` движка: (monotonic, данные) — для `stale`.
        self._last_stats: dict[str, tuple[float, dict]] = {}

    async def refresh(self) -> None:
        """Перечитать реестр из БД и пересобрать клиентов под новый состав движков."""
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                db_rows = await EngineRepository(session).list_enabled()
        except Exception as exc:  # noqa: BLE001
            log.warning("engine pool refresh: DB read failed: %s", exc)
            return
        db_rows = self._evict_stale(db_rows)
        new_specs = _merge_specs(self._static, db_rows)
        new_ids = {s.id for s in new_specs}
        async with self._lock:
            to_close: list[EngineClient] = []
            for eid in list(self._by_id):
                if eid not in new_ids:
                    to_close.append(self._by_id.pop(eid))
            by_id: dict[str, EngineClient] = {}
            for s in new_specs:
                existing = self._by_id.get(s.id)
                if existing is not None and existing.base_url == s.url.rstrip("/"):
                    by_id[s.id] = existing
                else:
                    if existing is not None:
                        to_close.append(existing)
                    by_id[s.id] = EngineClient(s.url)
            self._by_id = by_id
            self._specs = new_specs
        for client in to_close:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    def _evict_stale(self, db_rows):
        """Убрать из активного состава динамические движки без свежего heartbeat.

        Статически сконфигурированные движки (`engines.json`) всегда остаются — их наличие
        задаётся локальным конфигом, а не heartbeat'ом. Выбывают только самозарегистрированные
        движки, от которых не было сигнала дольше TTL (машина выключена/убрана)."""
        ttl = engine_ttl_seconds()
        now = datetime.now(timezone.utc)
        static_ids = {s.id for s in self._static}
        kept = []
        for r in db_rows:
            if r.id in static_ids:
                kept.append(r)
                continue
            ls = r.last_seen
            if ls is None:
                log.info("engine pool: drop never-seen dynamic engine %s", r.id)
                continue
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            age = (now - ls).total_seconds()
            if age <= ttl:
                kept.append(r)
            else:
                log.info(
                    "engine pool: evicting stale engine %s (last_seen %ds ago > ttl %ds)",
                    r.id, int(age), ttl,
                )
        return kept

    @property
    def specs(self) -> list[EngineSpec]:
        return list(self._specs)

    @property
    def static_ids(self) -> set[str]:
        return {s.id for s in self._static}

    @staticmethod
    def ttl_seconds() -> int:
        return engine_ttl_seconds()

    def resolve_engine_id(self, save_path: str) -> str:
        return resolve_engine_id(save_path, self._specs)

    def match_engine_id(self, save_path: str) -> str | None:
        return match_engine_id(save_path, self._specs)

    def client_for(self, engine_id: str) -> EngineClient:
        client = self._by_id.get(engine_id)
        if client is None:
            raise KeyError(f"unknown engine_id: {engine_id}")
        return client

    def client_for_path(self, save_path: str) -> tuple[str, EngineClient]:
        eid = self.resolve_engine_id(save_path)
        return eid, self.client_for(eid)

    def client_for_row(self, row) -> EngineClient:
        return self.client_for(row.engine_id)

    def spec(self, engine_id: str) -> EngineSpec | None:
        return spec_by_id(self._specs, engine_id)

    async def aclose(self) -> None:
        for client in self._by_id.values():
            await client.aclose()

    async def health_all(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for spec in self._specs:
            try:
                await self._by_id[spec.id].health()
                out[spec.id] = True
            except httpx.HTTPError:
                out[spec.id] = False
        return out

    async def session_stats_all(
        self, *, deadline: float | None = None, stale_ok: bool = False
    ) -> dict[str, dict]:
        """`session/stats` всех движков — параллельно, порядок ключей = порядок специй.

        deadline — потолок ожидания одного движка (сек); None — как раньше, таймаут клиента.
        stale_ok — если движок не ответил, отдать его последний удачный ответ (не старше
        `SEEDING_ENGINE_STATS_STALE_MAX`) с `stale: true` и `stale_age` (сек) вместо
        `{"error": true}`. Для UI; алерты и метрики зовут без него, чтобы видеть реальный down.
        """
        specs = list(self._specs)
        clients = {s.id: self._by_id[s.id] for s in specs}

        async def one(eid: str) -> dict:
            try:
                call = clients[eid].session_stats()
                data = await (asyncio.wait_for(call, deadline) if deadline else call)
            except (httpx.HTTPError, asyncio.TimeoutError, TimeoutError):
                return self._stale_or_error(eid) if stale_ok else {"error": True}
            if isinstance(data, dict) and not data.get("error"):
                self._last_stats[eid] = (time.monotonic(), data)
            return data

        results = await asyncio.gather(*(one(s.id) for s in specs))
        return {s.id: r for s, r in zip(specs, results)}

    def _stale_or_error(self, engine_id: str) -> dict:
        last = self._last_stats.get(engine_id)
        if last is not None:
            age = time.monotonic() - last[0]
            if age <= stats_stale_max_seconds():
                return {**last[1], "stale": True, "stale_age": int(age)}
        return {"error": True}

    async def session_stats_live(self) -> dict[str, dict]:
        """Для живых экранов (GET /session/stats, SSE, WS `stats`): короткий потолок на
        движок и последний известный ответ с пометкой `stale`, если движок не успел."""
        return await self.session_stats_all(deadline=stats_deadline_seconds(), stale_ok=True)

    async def set_session_limits_all(
        self, download_limit: int | None, upload_limit: int | None
    ) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for spec in self._specs:
            try:
                out[spec.id] = await self._by_id[spec.id].set_session_limits(
                    download_limit, upload_limit
                )
            except httpx.HTTPError as exc:
                out[spec.id] = {"error": str(exc)}
        return out

    @staticmethod
    def aggregate_session_stats(by_engine: dict[str, dict]) -> dict:
        total: dict[str, int] = {
            "torrents": 0,
            "torrents_active": 0,
            "download_rate": 0,
            "upload_rate": 0,
            "total_uploaded": 0,
            "total_downloaded": 0,
            "peers": 0,
            "seeds": 0,
            "leechers": 0,
        }
        engines_ok = 0
        engines_stale = 0
        for stats in by_engine.values():
            if stats.get("error"):
                continue
            engines_ok += 1
            if stats.get("stale"):
                engines_stale += 1
            for key in total:
                total[key] += int(stats.get(key) or 0)
        return {
            **total,
            "engines_ok": engines_ok,
            "engines_stale": engines_stale,
            "engines_total": len(by_engine),
            "by_engine": by_engine,
        }
