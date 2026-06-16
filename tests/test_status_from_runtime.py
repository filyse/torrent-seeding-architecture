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


def test_paused_is_truthful_for_complete_seed():
    """Ручная пауза готового сида должна отражаться честно (а не маскироваться под seeding).
    «Не зависать в паузе после рестарта» обеспечивают движок (auto_managed=False,
    неограниченные active_*) и restore (авто-resume сидов), а не подмена статуса здесь."""
    assert status_from_runtime("paused", "seeding", 1.0) == TorrentStatus.paused.value
    assert status_from_runtime("paused", "finished", 1.0) == TorrentStatus.paused.value
