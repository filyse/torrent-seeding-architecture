"""Карта WAN-каналов. Реализация в `seeding_db.wan_links` — её же читает queue-воркер."""

from seeding_db.wan_links import (
    WanLink,
    assign_engines,
    engine_wan_map,
    host_of,
    link_by_id,
    link_for_url,
    links,
)

__all__ = [
    "WanLink",
    "assign_engines",
    "engine_wan_map",
    "host_of",
    "link_by_id",
    "link_for_url",
    "links",
]
