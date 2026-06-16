"""Реестр движков: загрузка из env и маршрутизация по save_path."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineSpec:
    id: str
    url: str
    storage_prefix: str
    listen_port: int | None = None

    def normalized_prefix(self) -> str:
        return self.storage_prefix.replace("\\", "/").rstrip("/")


def _parse_specs(raw: str) -> list[EngineSpec]:
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise ValueError("ENGINES_CONFIG must be a non-empty JSON array")
    specs: list[EngineSpec] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each engine entry must be an object")
        eid = str(item.get("id", "")).strip()
        url = str(item.get("url", "")).strip().rstrip("/")
        prefix = str(item.get("storage_prefix", "")).strip()
        if not eid or not url or not prefix:
            raise ValueError("engine entry requires id, url, storage_prefix")
        lp = item.get("listen_port")
        listen_port = int(lp) if lp is not None else None
        specs.append(EngineSpec(id=eid, url=url, storage_prefix=prefix, listen_port=listen_port))
    return specs


def load_engine_specs() -> list[EngineSpec]:
    """
    ENGINES_CONFIG (JSON array) или ENGINES_CONFIG_FILE — multi-engine.
    Иначе fallback: один движок из ENGINE_URL + SEEDING_DATA_ROOT.
    """
    raw = os.getenv("ENGINES_CONFIG", "").strip()
    if not raw:
        path = os.getenv("ENGINES_CONFIG_FILE", "").strip()
        if path:
            with open(path, encoding="utf-8") as f:
                raw = f.read().strip()
    if raw:
        return _parse_specs(raw)
    url = os.getenv("ENGINE_URL", "http://127.0.0.1:8081").strip().rstrip("/")
    root = os.getenv("SEEDING_DATA_ROOT", "/data").strip()
    return [EngineSpec(id="default", url=url, storage_prefix=root)]


def normalize_save_path(path: str) -> str:
    """Нормализует путь сохранения: слэши, дубли слэшей, хвостовой слэш.
    Не делает путь абсолютным — это решает вызывающий код."""
    norm = (path or "").strip().replace("\\", "/")
    while "//" in norm:
        norm = norm.replace("//", "/")
    if len(norm) > 1:
        norm = norm.rstrip("/")
    return norm


def match_engine_id(save_path: str, specs: list[EngineSpec]) -> str | None:
    """Строгий матчинг по самому длинному совпавшему storage_prefix.
    Возвращает None, если путь не принадлежит ни одному движку (без молчаливого дефолта)."""
    norm = normalize_save_path(save_path)
    ordered = sorted(specs, key=lambda s: len(s.normalized_prefix()), reverse=True)
    for spec in ordered:
        prefix = spec.normalized_prefix()
        if norm == prefix or norm.startswith(prefix + "/"):
            return spec.id
    return None


def resolve_engine_id(save_path: str, specs: list[EngineSpec]) -> str:
    """Как match_engine_id, но с дефолтом (последний по длине префикса) при отсутствии совпадения.
    Используется там, где нужна обратная совместимость; новый код предпочитает match_engine_id."""
    matched = match_engine_id(save_path, specs)
    if matched is not None:
        return matched
    ordered = sorted(specs, key=lambda s: len(s.normalized_prefix()), reverse=True)
    return ordered[-1].id if ordered else "default"


def spec_by_id(specs: list[EngineSpec], engine_id: str) -> EngineSpec | None:
    for spec in specs:
        if spec.id == engine_id:
            return spec
    return None
