from seeding_db.models import TorrentStatus
from seeding_db.status_from_runtime import status_from_runtime


def test_seeding_from_lt_state():
    assert status_from_runtime("active", "seeding") == TorrentStatus.seeding.value


def test_seeding_from_progress_when_complete():
    assert status_from_runtime("active", "downloading", 1.0) == TorrentStatus.seeding.value
    assert status_from_runtime("active", "", 1.0) == TorrentStatus.seeding.value


def test_still_downloading_below_complete():
    assert status_from_runtime("active", "downloading", 0.5) == TorrentStatus.downloading.value


def test_paused_wins():
    assert status_from_runtime("paused", "seeding", 1.0) == TorrentStatus.paused.value
