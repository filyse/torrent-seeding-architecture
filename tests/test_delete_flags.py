from types import SimpleNamespace

from seeding_engine.torrent_runtime import LibtorrentTorrentRuntime


def test_libtorrent_delete_flags_uses_session_namespace():
    lt = SimpleNamespace(
        session=SimpleNamespace(delete_files=1, delete_partfile=2),
        remove_flags_t=SimpleNamespace(delete_files=4),
    )
    flags = LibtorrentTorrentRuntime._libtorrent_delete_flags(lt, True)
    assert flags & 1
    assert flags & 2
