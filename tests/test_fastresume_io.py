
from seeding_engine.fastresume_io import (
    delete_fastresume,
    ensure_engine_dirs,
    fastresume_dir,
    fastresume_path,
    session_state_path,
)


def test_fastresume_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("SEEDING_FASTRESUME_DIR", raising=False)
    assert fastresume_dir() == tmp_path / ".fastresume"
    assert fastresume_path(42) == tmp_path / ".fastresume" / "42.fastresume"


def test_fastresume_custom_dir(monkeypatch, tmp_path):
    custom = tmp_path / "fr"
    monkeypatch.setenv("SEEDING_FASTRESUME_DIR", str(custom))
    assert fastresume_dir() == custom


def test_session_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SEEDING_LT_STATE_FILE", str(tmp_path / "s.state"))
    assert session_state_path() == tmp_path / "s.state"


def test_ensure_engine_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("ENGINE_STORAGE_SUBDIR", "b3")
    ensure_engine_dirs()
    assert (tmp_path / ".state").is_dir()
    assert (tmp_path / ".fastresume").is_dir()
    assert (tmp_path / ".torrents").is_dir()
    assert (tmp_path / "b3").is_dir()


def test_delete_fastresume_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDING_DATA_ROOT", str(tmp_path))
    delete_fastresume(99)
