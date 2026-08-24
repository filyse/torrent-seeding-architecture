"""Карта WAN-каналов: через какой аплинк движок выходит в интернет.

Живёт в `seeding_db`, а не в API: queue-воркер не импортирует `seeding_api`
(`PYTHONPATH` без `/app/api`), но пишет сэмплы отдачи с той же раскладкой
движок → канал. API реэкспортирует этот модуль.

У нас не mwan3-балансировка, а физически раздельные каналы: каждый хост с движками
сидит за своим роутером и своим провайдером. Поэтому привязка «движок → канал»
однозначна и выводится из подсети в URL движка — реестр URL уже знает, и никакого
дополнительного опроса движков не нужно.

Топология и ёмкость аплинков переопределяются через `SEEDING_WAN_LINKS` — JSON-массив
объектов той же формы, что и `_DEFAULT_LINKS`, чтобы менять их без пересборки образа.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_GIGABIT = 1_000_000_000

_DEFAULT_LINKS: list[dict] = [
    {
        "id": "wan1",
        "name": "WAN1",
        "router": "R1",
        "wan_ip": "88.204.56.176",
        "subnets": ["192.168.1."],
        "capacity_bps": _GIGABIT,
    },
    {
        "id": "wan2",
        "name": "WAN2",
        "router": "R2",
        "wan_ip": "88.204.18.1",
        "subnets": ["192.168.2."],
        "capacity_bps": _GIGABIT,
    },
]


@dataclass(frozen=True)
class WanLink:
    id: str
    name: str
    router: str
    wan_ip: str
    subnets: tuple[str, ...]
    #: Пропускная способность аплинка в БИТАХ/с. Скорости движков libtorrent отдаёт
    #: в байтах/с — при расчёте утилизации их надо умножать на 8.
    capacity_bps: int

    def matches(self, host: str) -> bool:
        return any(host.startswith(prefix) for prefix in self.subnets if prefix)


def _parse(raw: list) -> list[WanLink]:
    out: list[WanLink] = []
    for item in raw:
        if not isinstance(item, dict):
            log.warning("skip malformed WAN link (not an object): %r", item)
            continue
        try:
            link_id = str(item["id"]).strip()
            if not link_id:
                raise ValueError("empty id")
            out.append(
                WanLink(
                    id=link_id,
                    name=str(item.get("name") or link_id).strip(),
                    router=str(item.get("router") or "").strip(),
                    wan_ip=str(item.get("wan_ip") or "").strip(),
                    subnets=tuple(str(s).strip() for s in (item.get("subnets") or [])),
                    capacity_bps=int(item.get("capacity_bps") or 0),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skip malformed WAN link %r: %s", item, exc)
    return out


def links() -> list[WanLink]:
    """Список каналов из `SEEDING_WAN_LINKS`, иначе — топология по умолчанию."""
    raw = os.getenv("SEEDING_WAN_LINKS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("SEEDING_WAN_LINKS is not valid JSON (%s) — using defaults", exc)
        else:
            if isinstance(parsed, list):
                return _parse(parsed)
            log.warning("SEEDING_WAN_LINKS is not a list — using defaults")
    return _parse(_DEFAULT_LINKS)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").strip()
    except ValueError:
        return ""


def link_for_url(url: str, all_links: list[WanLink] | None = None) -> WanLink | None:
    """Канал, за которым сидит движок с данным URL. None — подсеть неизвестна."""
    host = host_of(url)
    if not host:
        return None
    for link in links() if all_links is None else all_links:
        if link.matches(host):
            return link
    return None


def link_by_id(wan_id: str, all_links: list[WanLink] | None = None) -> WanLink | None:
    resolved = links() if all_links is None else all_links
    for link in resolved:
        if link.id == wan_id:
            return link
    return None


def assign_engines(
    engines: Iterable[tuple[str, str]],
    all_links: list[WanLink] | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Разложить движки (id, url) по каналам. Второй элемент — вне карты."""
    resolved = links() if all_links is None else all_links
    buckets: dict[str, list[str]] = {link.id: [] for link in resolved}
    unassigned: list[str] = []
    for engine_id, url in engines:
        link = link_for_url(url, resolved)
        if link is None:
            unassigned.append(engine_id)
        else:
            buckets[link.id].append(engine_id)
    return buckets, unassigned


def engine_wan_map(engines: Iterable[tuple[str, str]]) -> dict[str, str]:
    """id движка → id канала. Движки вне карты сюда не попадают."""
    buckets, _ = assign_engines(engines)
    out: dict[str, str] = {}
    for wan_id, ids in buckets.items():
        for engine_id in ids:
            out[engine_id] = wan_id
    return out
