import json

import pytest
from seeding_db.engine_registry import EngineSpec, load_engine_specs, resolve_engine_id


def test_resolve_engine_id_longest_prefix():
    specs = [
        EngineSpec(id="b1", url="http://e1", storage_prefix="/data/b1"),
        EngineSpec(id="b2", url="http://e2", storage_prefix="/data/b2"),
    ]
    assert resolve_engine_id("/data/b1/movie", specs) == "b1"
    assert resolve_engine_id("/data/b2/show", specs) == "b2"
    assert resolve_engine_id("/data/other", specs) == "b2"


def test_load_engine_specs_from_json(monkeypatch):
    cfg = json.dumps(
        [
            {"id": "b1", "url": "http://e1:8081", "storage_prefix": "/data/b1", "listen_port": 50001},
        ]
    )
    monkeypatch.setenv("ENGINES_CONFIG", cfg)
    monkeypatch.delenv("ENGINE_URL", raising=False)
    specs = load_engine_specs()
    assert len(specs) == 1
    assert specs[0].id == "b1"
    assert specs[0].listen_port == 50001


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
