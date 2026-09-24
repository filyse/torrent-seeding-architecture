"""Сиды роя по скрейпу трекера.

libtorrent ``num_seeds`` — только чужие сиды в коннектах. Анонс RuDub не
кладёт ``complete`` в ответ, поэтому ``num_complete`` остаётся -1, хотя
``scrape.php`` знает размер роя.
"""

from __future__ import annotations

import ssl
import urllib.parse
import urllib.request


def display_seed_count(num_complete: int | None) -> int | None:
    """Сколько сидов в рое по трекеру. None — скрейпа ещё не было."""
    if num_complete is None:
        return None
    try:
        n = int(num_complete)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def scrape_url(announce_url: str, info_hash: bytes) -> str | None:
    if "announce" not in announce_url or len(info_hash) != 20:
        return None
    url = announce_url.replace("announce", "scrape", 1)
    q = urllib.parse.urlencode({"info_hash": info_hash})
    return url + ("&" if "?" in url else "?") + q


def fetch_scrape(announce_url: str, info_hash: bytes, timeout: float = 8.0) -> tuple[int, int] | None:
    """(сиды, личи) с scrape.php. None — ответа нет."""
    url = scrape_url(announce_url, info_hash)
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "seeding-scrape"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        raw = resp.read()
    seeds = bdecode_int(raw, "complete")
    leechers = bdecode_int(raw, "incomplete")
    if seeds is None and leechers is None:
        return None
    return (seeds if seeds is not None else 0, leechers if leechers is not None else 0)


def bdecode_complete(raw: bytes) -> int | None:
    return bdecode_int(raw, "complete")


def bdecode_int(raw: bytes, field: str) -> int | None:
    key = f"{len(field)}:{field}i".encode()
    i = raw.find(key)
    if i < 0:
        return None
    j = raw.find(b"e", i + len(key))
    if j < 0:
        return None
    try:
        return int(raw[i + len(key) : j])
    except ValueError:
        return None
