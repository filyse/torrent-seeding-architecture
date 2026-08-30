"""HMAC download-ticket: api ↔ engine, не путать с upload-ticket."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "upload"))

from seeding_api.upload_ticket import (  # noqa: E402
    issue_download_ticket,
    issue_ticket,
    normalize_download_path,
)
from seeding_upload.ticket import TicketError, verify_download_ticket, verify_ticket  # noqa: E402


def test_download_ticket_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    token = issue_download_ticket(
        engine_id="b6",
        torrent_id=12041,
        path="Season 1/e01.mkv",
        uid="alice",
        ttl_seconds=300,
    )
    claims = verify_download_ticket(token, "test-secret")
    assert claims.engine_id == "b6"
    assert claims.torrent_id == 12041
    assert claims.path == "Season 1/e01.mkv"
    assert claims.uid == "alice"


def test_upload_ticket_is_not_download(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    token = issue_ticket(
        engine_id="b1",
        dest_dir="/data/b1",
        filename="a.bin",
        size=1,
        uid="x",
        ttl_seconds=600,
    )
    verify_ticket(token, "test-secret")
    with pytest.raises(TicketError):
        verify_download_ticket(token, "test-secret")


def test_download_ticket_expired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    token = issue_download_ticket(
        engine_id="b1",
        torrent_id=1,
        path="a.bin",
        uid="x",
        ttl_seconds=1,
    )
    with pytest.raises(TicketError):
        verify_download_ticket(token, "test-secret", now=10**12)


def test_normalize_download_path_rejects_escape():
    assert normalize_download_path("Season 1/e01.mkv") == "Season 1/e01.mkv"
    with pytest.raises(ValueError):
        normalize_download_path("../secrets")
    with pytest.raises(ValueError):
        normalize_download_path("/etc/passwd")
    with pytest.raises(ValueError):
        normalize_download_path("foo/../../etc/passwd")
