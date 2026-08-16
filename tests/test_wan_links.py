import json

from seeding_api import wan_links


def test_default_topology_maps_engines_to_uplinks():
    """b* (LAN1) уходят в WAN1, a* (LAN2) — в WAN2; это и есть основа экрана «Сеть»."""
    links = wan_links.links()
    by_id = {link.id: link for link in links}
    assert set(by_id) == {"wan1", "wan2"}

    b1 = wan_links.link_for_url("https://192.168.1.171:8081", links)
    a1 = wan_links.link_for_url("https://192.168.2.243:8081", links)
    assert b1 is not None and b1.id == "wan1"
    assert a1 is not None and a1.id == "wan2"


def test_capacity_is_in_bits_per_second():
    """Ёмкость хранится в битах/с — UI умножает байты/с движка на 8, чтобы посчитать %."""
    for link in wan_links.links():
        assert link.capacity_bps == 1_000_000_000


def test_unknown_subnet_is_unassigned():
    assert wan_links.link_for_url("https://10.0.0.5:8081") is None
    assert wan_links.link_for_url("") is None
    assert wan_links.link_for_url("not a url") is None


def test_env_override_replaces_topology(monkeypatch):
    monkeypatch.setenv(
        "SEEDING_WAN_LINKS",
        json.dumps(
            [
                {
                    "id": "solo",
                    "name": "Solo",
                    "router": "R9",
                    "wan_ip": "1.2.3.4",
                    "subnets": ["10.0.0."],
                    "capacity_bps": 500_000_000,
                }
            ]
        ),
    )
    links = wan_links.links()
    assert [link.id for link in links] == ["solo"]
    found = wan_links.link_for_url("https://10.0.0.5:8081", links)
    assert found is not None and found.capacity_bps == 500_000_000
    # Прежняя подсеть больше не сопоставляется — карта полностью заменена.
    assert wan_links.link_for_url("https://192.168.1.171:8081", links) is None


def test_broken_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("SEEDING_WAN_LINKS", "{not json")
    assert {link.id for link in wan_links.links()} == {"wan1", "wan2"}

    monkeypatch.setenv("SEEDING_WAN_LINKS", json.dumps({"id": "obj-not-list"}))
    assert {link.id for link in wan_links.links()} == {"wan1", "wan2"}


def test_assign_engines_groups_by_uplink():
    buckets, unassigned = wan_links.assign_engines(
        [
            ("b2", "https://192.168.1.171:8082"),
            ("a1", "https://192.168.2.243:8081"),
            ("b1", "https://192.168.1.171:8081"),
            ("x1", "https://10.0.0.5:8081"),
        ]
    )
    assert buckets["wan1"] == ["b2", "b1"]
    assert buckets["wan2"] == ["a1"]
    assert unassigned == ["x1"]
    assert wan_links.link_by_id("wan1") is not None
    assert wan_links.link_by_id("missing") is None


def test_malformed_entries_are_skipped_not_fatal(monkeypatch):
    monkeypatch.setenv(
        "SEEDING_WAN_LINKS",
        json.dumps([{"no_id": True}, {"id": "ok", "subnets": ["172.16."]}]),
    )
    links = wan_links.links()
    assert [link.id for link in links] == ["ok"]
    # Имя подставляется из id, ёмкость без значения = 0 (шкалу утилизации UI не рисует).
    assert links[0].name == "ok"
    assert links[0].capacity_bps == 0
