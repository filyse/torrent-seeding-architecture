"""Сиды в списке — число роя со скрейпа, не «мы сами» и не чужие коннекты."""

from seeding_api.engine_pool import EnginePool
from seeding_api.runtime_sync import runtime_from_snapshot
from seeding_api.seed_count import display_seed_count as api_display
from seeding_engine.seed_count import bdecode_complete, display_seed_count as engine_display, scrape_url


def test_unknown_scrape_is_not_zero():
    assert api_display(None) is None
    assert api_display(-1) is None
    assert engine_display(None) is None
    assert engine_display(-1) is None


def test_tracker_complete_is_the_swarm():
    assert api_display(6) == 6
    assert engine_display(6) == 6
    assert api_display(0) == 0


def test_scrape_url_and_complete_field():
    ih = bytes.fromhex("afb5ae9679d3fe3194827a5949757481525520cf")
    url = scrape_url("https://tr.example/announce.php?passkey=abc", ih)
    assert url is not None
    assert url.startswith("https://tr.example/scrape.php?passkey=abc&info_hash=")
    raw = b"d8:completei6e10:incompletei2ee"
    assert bdecode_complete(raw) == 6
    from seeding_engine.seed_count import bdecode_int
    assert bdecode_int(raw, "incomplete") == 2
    assert bdecode_complete(b"d8:intervali1800ee") is None


class _Row:
    def __init__(self, **kwargs):
        self.id = 1
        self.magnet_uri = None
        self.save_path = "/data/x"
        self.status = "seeding"
        self.info_hash = "aa"
        self.progress = 1.0
        self.down_rate = 0
        self.up_rate = 0
        self.uploaded_total = 0
        self.peers = 2
        self.display_name = "show"
        self.size = 100
        self.seeds = None
        self.leechers = None
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_snapshot_without_scrape_leaves_seeds_empty():
    rt = runtime_from_snapshot(_Row())
    assert rt["num_seeds"] is None
    assert rt["num_leechers"] is None


def test_snapshot_uses_tracker_swarm():
    rt = runtime_from_snapshot(_Row(seeds=6, leechers=2))
    assert rt["num_seeds"] == 6
    assert rt["num_leechers"] == 2


def test_aggregate_session_stats_sums_seeds_and_peers():
    out = EnginePool.aggregate_session_stats(
        {
            "a1": {
                "torrents": 2,
                "torrents_active": 2,
                "download_rate": 0,
                "upload_rate": 10,
                "total_uploaded": 1,
                "total_downloaded": 0,
                "seeds": 6,
                "peers": 2,
            },
            "b1": {"error": True, "seeds": 99, "peers": 99},
        }
    )
    assert out["seeds"] == 6
    assert out["peers"] == 2
