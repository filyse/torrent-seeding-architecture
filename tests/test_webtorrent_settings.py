"""WebTorrent глушится только ключами, которые сессия уже знает."""

from seeding_engine.torrent_runtime import webtorrent_off_settings


def test_webtorrent_off_skips_unknown_names():
    assert webtorrent_off_settings(set()) == {}
    assert webtorrent_off_settings({"enable_dht"}) == {}


def test_webtorrent_off_uses_stun_and_offers():
    got = webtorrent_off_settings(
        {"webtorrent_stun_server", "max_webtorrent_offers", "enable_dht"}
    )
    assert got == {"webtorrent_stun_server": "", "max_webtorrent_offers": 0}
