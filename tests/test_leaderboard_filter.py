"""/leaderboard?q=: name filter; rows keep their rank on the unfiltered board."""
import json

import pytest
from fastapi.testclient import TestClient

from api import cache, merges as mg
from api.main import create_app

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "aaaaaaaa-0000-4000-8000-000000000002"
C = "aaaaaaaa-0000-4000-8000-000000000003"
D = "aaaaaaaa-0000-4000-8000-000000000004"
LB = "/api/stats/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone&scope=cmp"


@pytest.fixture
def env(world, run_snapshot, monkeypatch, tmp_path):
    world.set_usercache("cmp", {A: "Zed", B: "Bob", C: "Al_ice", D: "Cara"})
    for u, n in ((A, 40), (B, 30), (C, 20), (D, 10)):
        world.add_stats("cmp", u, {"minecraft:mined": {"minecraft:stone": n}})
    run_snapshot(1000)
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"renames": [{"uuid": D, "names": ["OldName_x"]}]}), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    mg.reset_api_merges()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def get(env, **params):
    """httpx replaces a URL's query with params=, so merge them here."""
    base = dict(x.split("=", 1) for x in LB.split("?", 1)[1].split("&"))
    return env.get(LB.split("?")[0], params={**base, **params}).json()


def rows(j):
    return [(r["rank"], r["name"]) for r in j["rows"]]


def test_filter_keeps_real_rank(env):
    j = get(env, q="AL ICE")
    assert rows(j) == [(3, "Al_ice")] and j["total_count"] == 1


def test_filter_no_match_and_paging(env):
    j = get(env, q="zzz")
    assert j["rows"] == [] and j["total_count"] == 0
    j = get(env, q="b", limit=1, offset=0)
    assert j["total_count"] == 1 and rows(j) == [(2, "Bob")]


def test_filter_matches_former_name(env):
    assert rows(get(env, q="oldname x")) == [(4, "Cara")]


def test_blank_q_is_no_filter(env):
    assert get(env, q="  ")["total_count"] == 4
