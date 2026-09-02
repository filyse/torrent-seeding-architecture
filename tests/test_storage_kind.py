import os

from seeding_engine.sysinfo import classify_storage_path, storage_kind
from seeding_engine.upload_hold import SessionUploadGate, should_hold_for_disk


def test_storage_kind_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SEEDING_STORAGE_KIND", "hdd")
    monkeypatch.delenv("SEEDING_ENGINE_STORAGE_PREFIX", raising=False)
    assert storage_kind(str(tmp_path)) == "hdd"
    monkeypatch.setenv("SEEDING_STORAGE_KIND", "ssd")
    assert storage_kind(str(tmp_path)) == "ssd"


def test_classify_from_rotational(monkeypatch):
    monkeypatch.setattr("seeding_engine.sysinfo.rotational_for_path", lambda _p: True)
    assert classify_storage_path("/data/b1") == "hdd"
    monkeypatch.setattr("seeding_engine.sysinfo.rotational_for_path", lambda _p: False)
    assert classify_storage_path("/data/a1") == "ssd"
    monkeypatch.setattr("seeding_engine.sysinfo.rotational_for_path", lambda _p: None)
    assert classify_storage_path("/data/x") == "unknown"


def test_classify_unknown_without_sysfs(monkeypatch, tmp_path):
    # Нет os.major (Windows) или нет /sys — unknown, не SSD. Hold тогда включится.
    monkeypatch.setattr("seeding_engine.sysinfo.rotational_for_path", lambda _p: None)
    assert classify_storage_path(str(tmp_path)) == "unknown"


def test_should_hold_hdd_and_unknown_not_ssd():
    assert should_hold_for_disk("hdd", 1024 * 1024) is True
    assert should_hold_for_disk("unknown", 1024 * 1024) is True
    assert should_hold_for_disk("ssd", 1024 * 1024) is False
    assert should_hold_for_disk("hdd", 0) is False


def test_upload_hold_caps_unlimited_and_high_desired():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    assert gate.begin_create("hdd") is True
    assert applied[-1] == 1024 * 1024
    gate.set_desired(20 * 1024 * 1024)
    assert applied[-1] == 1024 * 1024
    gate.end_create()
    assert applied[-1] == 20 * 1024 * 1024


def test_upload_hold_keeps_stricter_user_limit():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=256 * 1024, cap_bps=1024 * 1024)
    assert gate.begin_create("hdd") is True
    assert applied[-1] == 256 * 1024
    gate.end_create()
    assert applied[-1] == 256 * 1024


def test_upload_hold_skipped_on_ssd():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    assert gate.begin_create("ssd") is False
    assert applied == []
    gate.end_create()
    assert applied == []


def test_upload_hold_refcount_nested():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1_000_000)
    assert gate.begin_create("hdd") is True
    assert gate.begin_create("hdd") is True
    gate.end_create()
    assert gate.hold.active is True
    assert applied[-1] == 1_000_000
    gate.end_create()
    assert gate.hold.active is False
    assert applied[-1] == 0
