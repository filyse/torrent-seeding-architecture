"""Очередь проверки файлов: слоты, классификация, пауза пользователя."""

from pathlib import Path

from seeding_engine.check_admit import CheckAdmit, UserPauseStore


def test_full_check_waits_until_seeds_are_classified():
    admit = CheckAdmit(1)
    admit.hold(1)
    admit.hold(2)
    first = admit.tick({1: "checking_files"}, busy=0, now=0)
    assert first.resume == [1]
    assert first.pause == []

    parked = admit.tick({1: "checking_files"}, busy=0, now=1)
    assert parked.pause == [1]
    assert parked.resume == [2]
    assert admit.is_waiting(1)
    assert not admit.is_waiting(2)

    # Пока есть непроверенные, полный проход не стартует.
    still = admit.tick({2: "checking_resume_data"}, busy=0, now=1.2)
    assert still.resume == []
    assert admit.counts()["queued"] == 1
    assert admit.counts()["hashing"] == 0


def test_seed_releases_slot_for_the_next():
    admit = CheckAdmit(1)
    admit.hold(1)
    admit.hold(2)
    admit.tick({}, busy=0, now=0)
    nxt = admit.tick({1: "seeding"}, busy=0, now=0.2)
    assert nxt.resume == [2]
    assert not admit.is_waiting(1)
    assert admit.counts()["held"] == 0


def test_queued_hash_starts_only_after_classification():
    admit = CheckAdmit(1)
    admit.hold(1)
    admit.tick({}, busy=0, now=0)
    started = admit.tick({1: "checking_files"}, busy=0, now=0.2)
    assert started.pause == []
    assert started.resume == []
    assert admit.counts()["held"] == 0
    assert admit.counts()["hashing"] == 1
    assert not admit.is_waiting(1)


def test_explicit_recheck_consumes_the_same_slots():
    admit = CheckAdmit(1)
    admit.hold(1)
    blocked = admit.tick({}, busy=1, now=0)
    assert blocked.resume == []
    assert admit.is_waiting(1)
    opened = admit.tick({}, busy=0, now=1)
    assert opened.resume == [1]


def test_hash_slot_stays_for_a_long_check():
    admit = CheckAdmit(1, probe_timeout=8)
    admit.hold(1)
    admit.tick({}, busy=0, now=0)
    admit.tick({1: "checking_files"}, busy=0, now=0.2)
    later = admit.tick({1: "checking_files"}, busy=0, now=100)
    assert later.resume == []
    assert admit.counts()["hashing"] == 1


def test_running_slot_is_not_waiting():
    admit = CheckAdmit(1)
    admit.hold(1)
    admit.tick({}, busy=0, now=0)
    assert admit.is_running(1)
    assert not admit.is_waiting(1)
    admit = CheckAdmit(1)
    admit.hold(7)
    admit.drop(7)
    assert admit.tick({}, busy=0, now=0).resume == []
    assert not admit.is_waiting(7)


def test_user_pause_store_survives_seal(tmp_path: Path):
    path = tmp_path / "user-paused.json"
    fresh = UserPauseStore(path)
    assert fresh.ready is False
    fresh.note_legacy(4)
    fresh.seal()
    assert fresh.ready is True
    assert fresh.is_user(4)

    again = UserPauseStore(path)
    assert again.ready is True
    assert again.is_user(4)
    assert again.is_user(5) is False
    again.mark(5)
    again.clear(4)
    stored = UserPauseStore(path)
    assert stored.ids == {5}
