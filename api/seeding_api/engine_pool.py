"""Пул HTTP-клиентов к нескольким движкам."""

from __future__ import annotations

import httpx
from seeding_db.engine_registry import EngineSpec, load_engine_specs, resolve_engine_id, spec_by_id

from seeding_api.engine_client import EngineClient


class EnginePool:
    def __init__(self, specs: list[EngineSpec] | None = None) -> None:
        self._specs = specs if specs is not None else load_engine_specs()
        self._by_id: dict[str, EngineClient] = {s.id: EngineClient(s.url) for s in self._specs}

    @property
    def specs(self) -> list[EngineSpec]:
        return list(self._specs)

    def resolve_engine_id(self, save_path: str) -> str:
        return resolve_engine_id(save_path, self._specs)

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
