import json

import pytest
from seeding_db.engine_registry import (
    EngineSpec,
    load_engine_specs,
    match_engine_id,
    normalize_save_path,
    resolve_engine_id,
)


def test_resolve_engine_id_longest_prefix():
    specs = [
        EngineSpec(id="b1", url="http://e1", storage_prefix="/data/b1"),
        EngineSpec(id="b2", url="http://e2", storage_prefix="/data/b2"),
    ]
    assert resolve_engine_id("/data/b1/movie", specs) == "b1"
    assert resolve_engine_id("/data/b2/show", specs) == "b2"
    # обратная совместимость: несовпавший путь уходит в дефолт (последний по длине префикса)
    assert resolve_engine_id("/data/other", specs) == "b2"


def test_normalize_save_path():
    assert normalize_save_path("  /data/b1/  ") == "/data/b1"
    assert normalize_save_path("/data\\b1\\movie") == "/data/b1/movie"
    assert normalize_save_path("/data//b1///x") == "/data/b1/x"
    assert normalize_save_path("/") == "/"
    assert normalize_save_path("relative/path") == "relative/path"


def test_match_engine_id_strict_no_default():
    specs = [
        EngineSpec(id="b1", url="http://e1", storage_prefix="/data/b1"),
        EngineSpec(id="b2", url="http://e2", storage_prefix="/data/b2"),
    ]
    assert match_engine_id("/data/b1", specs) == "b1"
    assert match_engine_id("/data/b1/movie", specs) == "b1"
    assert match_engine_id("/data/b2/", specs) == "b2"
    # не принадлежит ни одному движку → None (без молчаливого дефолта)
    assert match_engine_id("/data/other", specs) is None
    assert match_engine_id("/srv/x", specs) is None
    # частичное совпадение имени не считается (b1x не под /data/b1)
    assert match_engine_id("/data/b1x", specs) is None


def test_load_engine_specs_from_json(monkeypatch):
    cfg = json.dumps(
        [
            {
                "id": "b1",
                "url": "http://e1:8081",
                "storage_prefix": "/data/b1",
                "listen_port": 50001,
                "media_path": "/media/seeding-test/b1/b1",
            },
        ]
    )
    monkeypatch.setenv("ENGINES_CONFIG", cfg)
    monkeypatch.delenv("ENGINE_URL", raising=False)
    specs = load_engine_specs()
    assert len(specs) == 1
    assert specs[0].id == "b1"
    assert specs[0].listen_port == 50001
    assert specs[0].media_path == "/media/seeding-test/b1/b1"
    assert specs[0].normalized_media_path() == "/media/seeding-test/b1/b1"


def test_media_path_optional_and_normalized():
    spec = EngineSpec(id="b1", url="http://e1", storage_prefix="/data/b1")
    assert spec.normalized_media_path() is None
    spec2 = EngineSpec(
        id="b2", url="http://e2", storage_prefix="/data/b2", media_path="/media/x/b2/"
    )
    assert spec2.normalized_media_path() == "/media/x/b2"


def test_load_engine_specs_fallback(monkeypatch):
    monkeypatch.delenv("ENGINES_CONFIG", raising=False)
    monkeypatch.setenv("ENGINE_URL", "http://solo:8081")
    monkeypatch.setenv("SEEDING_DATA_ROOT", "/data")
    specs = load_engine_specs()
    assert specs[0].id == "default"


def test_load_engine_specs_invalid(monkeypatch):
    monkeypatch.setenv("ENGINES_CONFIG", "[]")
    with pytest.raises(ValueError):
        load_engine_specs()
