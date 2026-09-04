import os

import time

from seeding_engine.sysinfo import classify_storage_path, storage_kind
from seeding_engine.upload_hold import (
    CheckHoldTracker,
    SessionUploadGate,
    is_full_hash_check_state,
    should_hold_for_disk,
)


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


def test_full_hash_check_states_skip_resume_and_seed():
    assert is_full_hash_check_state("checking") is True
    assert is_full_hash_check_state("checking_files") is True
    assert is_full_hash_check_state("queued_for_checking") is True
    assert is_full_hash_check_state("CHECKING_FILES") is True
    assert is_full_hash_check_state("checking_resume_data") is False
    assert is_full_hash_check_state("allocating") is False
    assert is_full_hash_check_state("seeding") is False
    assert is_full_hash_check_state("") is False


def test_check_hold_caps_while_checking_then_restores():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    tracker = CheckHoldTracker()
    tracker.sync(gate, "hdd", {7})
    assert applied[-1] == 1024 * 1024
    tracker.sync(gate, "hdd", set())
    assert applied[-1] == 0


def test_check_hold_skipped_on_ssd():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    tracker = CheckHoldTracker()
    tracker.sync(gate, "ssd", {1, 2})
    assert applied == []
    tracker.sync(gate, "ssd", set())
    assert applied == []


def test_check_hold_nests_with_create():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1_000_000)
    tracker = CheckHoldTracker()
    assert gate.begin_create("hdd") is True
    tracker.sync(gate, "hdd", {3})
    assert gate.hold.active is True
    tracker.sync(gate, "hdd", set())
    assert gate.hold.active is True
    gate.end_create()
    assert gate.hold.active is False
    assert applied[-1] == 0


def test_note_recheck_keeps_hold_until_grace_expires():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    tracker = CheckHoldTracker(pending_grace_s=0.2)
    tracker.note_recheck(gate, "hdd", 9)
    assert applied[-1] == 1024 * 1024
    tracker.sync(gate, "hdd", set())
    assert applied[-1] == 1024 * 1024
    time.sleep(0.25)
    tracker.sync(gate, "hdd", set())
    assert applied[-1] == 0


def test_note_recheck_handoff_to_checking_state():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    tracker = CheckHoldTracker(pending_grace_s=0.01)
    tracker.note_recheck(gate, "hdd", 9)
    tracker.sync(gate, "hdd", {9})
    time.sleep(0.05)
    tracker.sync(gate, "hdd", {9})
    assert applied[-1] == 1024 * 1024
    tracker.sync(gate, "hdd", set())
    assert applied[-1] == 0


def test_observe_one_torrent_does_not_drop_other_checking():
    applied: list[int] = []
    gate = SessionUploadGate(apply=applied.append, desired=0, cap_bps=1024 * 1024)
    tracker = CheckHoldTracker()
    tracker.observe(gate, "hdd", 1, "checking_files")
    tracker.observe(gate, "hdd", 2, "checking_files")
    assert applied[-1] == 1024 * 1024
    tracker.observe(gate, "hdd", 1, "seeding")
    assert gate.hold.active is True
    tracker.observe(gate, "hdd", 2, "seeding")
    assert gate.hold.active is False
    assert applied[-1] == 0
