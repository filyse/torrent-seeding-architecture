"""Контракт HMAC upload-ticket (api ↔ upload service)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "upload"))

from seeding_api.upload_ticket import (  # noqa: E402
    issue_ticket,
    normalize_dest_dir,
    resolve_upload_base_url,
    upload_per_engine,
)
from seeding_upload.ticket import TicketError, verify_ticket  # noqa: E402


def test_ticket_roundtrip(monkeypatch):
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    token = issue_ticket(
        engine_id="b1",
        dest_dir="/data/b1/shows",
        filename="ep01.mkv",
        size=123456789,
        uid="alice",
        ttl_seconds=600,
    )
    claims = verify_ticket(token, "test-secret")
    assert claims.engine_id == "b1"
    assert claims.dest_dir == "/data/b1/shows"
    assert claims.filename == "ep01.mkv"
    assert claims.size == 123456789
    assert claims.uid == "alice"


def test_ticket_bad_sig(monkeypatch):
    monkeypatch.setenv("SEEDING_UPLOAD_TICKET_SECRET", "test-secret")
    token = issue_ticket(
        engine_id="b1",
        dest_dir="/data/b1",
        filename="a.bin",
        size=1,
        uid="x",
        ttl_seconds=600,
    )
    try:
        verify_ticket(token, "other-secret")
        assert False, "expected TicketError"
    except TicketError:
        pass


def test_normalize_dest_dir_ok():
    assert normalize_dest_dir("/data/b1", "/data/b1/foo") == "/data/b1/foo"


def test_normalize_dest_dir_rejects_outside():
    try:
        normalize_dest_dir("/data/b1", "/data/b2/foo")
        assert False
    except ValueError:
        pass


def test_resolve_base_sidecar(monkeypatch):
    monkeypatch.delenv("SEEDING_UPLOAD_PER_ENGINE", raising=False)
    assert not upload_per_engine()
    assert (
        resolve_upload_base_url("b1", {"b1": "https://seedbox2.hw-s.ru/u/b"})
        == "https://seedbox2.hw-s.ru/u/b"
    )


def test_resolve_base_per_engine_appends_id(monkeypatch):
    monkeypatch.setenv("SEEDING_UPLOAD_PER_ENGINE", "1")
    assert (
        resolve_upload_base_url("b1", {"b1": "https://seedbox2.hw-s.ru/u/b"})
        == "https://seedbox2.hw-s.ru/u/b/b1"
    )
    assert (
        resolve_upload_base_url("b1", {"b1": "https://seedbox2.hw-s.ru/u/b/b1"})
        == "https://seedbox2.hw-s.ru/u/b/b1"
    )


def test_resolve_base_template(monkeypatch):
    monkeypatch.setenv("SEEDING_UPLOAD_PER_ENGINE", "0")
    assert (
        resolve_upload_base_url(
            "a2",
            {"*": "https://seedbox2.hw-s.ru/u/{contour}/{engine_id}"},
        )
        == "https://seedbox2.hw-s.ru/u/a/a2"
    )
