from seeding_engine.check_rate import CheckRate


def test_rate_is_bytes_over_the_window():
    rate = CheckRate(window_s=8)
    rate.add(1, 8 * 1024 * 1024, now=1)
    rate.add(1, 8 * 1024 * 1024, now=3)
    assert rate.rate(1, now=4) == 2 * 1024 * 1024


def test_old_pieces_leave_the_window():
    rate = CheckRate(window_s=8)
    rate.add(1, 80 * 1024 * 1024, now=0)
    assert rate.rate(1, now=9) == 0


def test_other_torrent_is_separate():
    rate = CheckRate(window_s=8)
    rate.add(1, 8 * 1024 * 1024, now=1)
    assert rate.rate(2, now=1) == 0
