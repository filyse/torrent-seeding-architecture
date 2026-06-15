from seeding_db.models import TorrentStatus
from seeding_db.status_from_runtime import status_from_runtime


def test_seeding_from_lt_state():
    assert status_from_runtime("active", "seeding") == TorrentStatus.seeding.value


def test_seeding_from_progress_when_complete():
    assert status_from_runtime("active", "downloading", 1.0) == TorrentStatus.seeding.value
    assert status_from_runtime("active", "", 1.0) == TorrentStatus.seeding.value


def test_still_downloading_below_complete():
    assert status_from_runtime("active", "downloading", 0.5) == TorrentStatus.downloading.value


def test_paused_wins_when_incomplete():
    assert status_from_runtime("paused", "downloading", 0.5) == TorrentStatus.paused.value


def test_complete_seed_not_stuck_paused():
    """После restore готовый сид не должен оставаться paused в БД."""
    assert status_from_runtime("paused", "seeding", 1.0) == TorrentStatus.seeding.value
    assert status_from_runtime("paused", "finished", 1.0) == TorrentStatus.seeding.value
