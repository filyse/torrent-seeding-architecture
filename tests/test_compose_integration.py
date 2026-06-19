"""
Живой стек: `docker compose up` и переменная окружения SEEDING_RUN_COMPOSE_TESTS=1.

По умолчанию тесты пропускаются (не требуют Docker).
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API_BASE = os.environ.get("SEEDING_INTEGRATION_API_URL", "http://127.0.0.1:8000").rstrip("/")
ENGINE_BASE = os.environ.get("SEEDING_INTEGRATION_ENGINE_URL", "http://127.0.0.1:8081").rstrip("/")


def _compose_enabled() -> bool:
    return os.environ.get("SEEDING_RUN_COMPOSE_TESTS", "").strip().lower() in ("1", "true", "yes", "on")


skip_unless_compose = pytest.mark.skipif(
    not _compose_enabled(),
    reason="Set SEEDING_RUN_COMPOSE_TESTS=1 with stack up (see tests/test_compose_integration.py docstring)",
)


@skip_unless_compose
def test_live_api_health():
    r = httpx.get(f"{API_BASE}/api/v1/health", timeout=10.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("service") == "api"
    assert body.get("status") in ("ok", "degraded")


@skip_unless_compose
def test_live_api_torrents_list_shape():
    r = httpx.get(f"{API_BASE}/api/v1/torrents", timeout=10.0)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert isinstance(data["items"], list)
    assert "total" in data


@skip_unless_compose
def test_live_engine_health():
    r = httpx.get(f"{ENGINE_BASE}/health", timeout=10.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("service") == "engine"
    assert body.get("backend") in ("mock", "libtorrent")
