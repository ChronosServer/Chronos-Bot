"""player_merges.json `renames`: a uuid's former scoreboard names (renames of
one account; scoreboards key by name)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache, merges as mg
from api.main import create_app

U = "bb8d717d-1e2f-4204-8ea5-88c90a60bca4"   # NotQuiteRat
V = "5f2a15be-cdcc-49a6-a74a-1b31e0562786"   # Diggus
OLD = "c273fdfb-2181-47be-b174-6d8f20489b1b"  # merged-away account
NEW = "2099-01-01 00:00:00 +0000"
D2021 = 1635426651      # 2021-10-28
D2023 = 1702755455      # 2023-12-16


def raw(**kw):
    base = {"merges": [{"from": OLD, "to": V, "names": ["OldAcc_"]}],
            "renames": [{"uuid": U, "names": ["NotQuiteRat_"]}]}
    base.update(kw)
    return base


def test_parse_renames_and_former_names():
    m = mg.parse_merges(raw())
    assert m.rename_target("notquiterat_", D2021) == U
    assert m.rename_target("NotQuiteRat_", D2023) == U
    assert m.rename_target("someoneelse", D2021) is None
    assert m.former_names(U) == ["NotQuiteRat_"]
    assert m.former_names(V) == ["OldAcc_"]
    assert m.merged_from(V) == [OLD]
    assert m.former_names(OLD) == []
    assert bool(mg.parse_merges({"renames": [{"uuid": U, "names": ["x"]}]}))


def test_rename_date_bounds():
    m = mg.parse_merges(raw(renames=[
        {"uuid": U, "names": ["Shared"], "from": "2021-01-01", "until": "2021-12-31"},
        {"uuid": V, "names": ["Shared"], "from": "2022-01-01"}]))
    assert m.rename_target("shared", D2021) == U
    assert m.rename_target("shared", D2023) == V
    assert m.rename_target("shared", 1609459199) is None  # 2020-12-31 23:59:59
    assert m.rename_target("shared", 1640995199) == U     # 2021-12-31 23:59:59 (until inclusive)
    assert m.rename_target("shared") in (U, V)            # no date: any entry


@pytest.mark.parametrize("bad", [
    {"renames": [{"uuid": "nope", "names": ["a"]}]},
    {"renames": [{"uuid": U, "names": []}]},
    {"renames": [{"uuid": U, "names": ["a"], "from": "2021-13-01"}]},
    {"renames": [{"uuid": U, "names": ["a"], "from": "2022-01-01", "until": "2021-01-01"}]},
    {"renames": [{"uuid": U, "names": ["OldAcc_"]}],
     "merges": [{"from": OLD, "to": V, "names": ["OldAcc_"]}]},
    {"renames": [{"uuid": U, "names": ["a"]}, {"uuid": V, "names": ["A"]}]},
    {"renames": "x"},
])
def test_parse_rename_errors(bad):
    with pytest.raises(ValueError):
        mg.parse_merges(bad)


def test_same_uuid_twice_is_fine():
    m = mg.parse_merges({"renames": [{"uuid": U, "names": ["a"]}, {"uuid": U, "names": ["b"]}]})
    assert m.former_names(U) == ["a", "b"]


@pytest.fixture
def api(world, run_snapshot, monkeypatch, tmp_path):
    world.set_usercache("cmp", {U: "NotQuiteRat", V: "Diggus", OLD: "OldAcc_"})
    world.add_stats("cmp", U, {"minecraft:mined": {"minecraft:stone": 5}})
    world.add_stats("cmp", V, {"minecraft:mined": {"minecraft:stone": 6}})
    world.add_stats("cmp", OLD, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(raw()), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    mg.reset_api_merges()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def test_player_aka_and_old_name_lookup(api):
    j = api.get("/api/stats/player/NotQuiteRat_").json()
    assert j["uuid"] == U and j["aka"] == ["NotQuiteRat_"]
    j = api.get(f"/api/stats/player/{V}").json()
    assert j["aka"] == ["OldAcc_"]


def test_blocked_former_name(world, run_snapshot):
    from api import names
    names.BLOCKLIST_PATH.write_text("notquite\n", encoding="utf-8")
    world.set_usercache("cmp", {V: "Diggus"})
    world.add_stats("cmp", V, {"minecraft:mined": {"minecraft:stone": 6}})
    run_snapshot(1000, merges=mg.parse_merges({"renames": [{"uuid": V, "names": ["NotQuite_"]}]}))
    c = sqlite3.connect(world.db_path)
    assert c.execute("SELECT uuid, name FROM blocked_players").fetchall() == [(V, "NotQuite_")]
    c.close()
