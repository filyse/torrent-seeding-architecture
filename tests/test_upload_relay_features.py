"""Контракт ticket route=relay / features.relay_enabled."""

from __future__ import annotations

import os

import pytest

from seeding_api.upload_ticket import upload_relay_base_urls, upload_relay_enabled


def test_relay_urls_empty_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEEDING_UPLOAD_RELAY_BASE_URLS", raising=False)
    assert upload_relay_base_urls() == {}
    assert upload_relay_enabled() is False


def test_relay_urls_parsed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "SEEDING_UPLOAD_RELAY_BASE_URLS",
        '{"a1":"https://relay.example/u/a","b1":"https://relay.example/u/b/"}',
    )
    urls = upload_relay_base_urls()
    assert urls["a1"] == "https://relay.example/u/a"
    assert urls["b1"] == "https://relay.example/u/b"
    assert upload_relay_enabled() is True
