import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
U4 = "44444444-4444-4444-4444-444444444444"
ST = "/api/stats/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "Bob", U3: "Botty", U4: "Dan"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice", "Bob", "Dan"]})
    world.add_stats("smp", U1, stone(5))
    world.add_stats("smp", U3, stone(100))
    world.add_stats("smp", U4, stone(5))
    run_snapshot(1_000_000)
    world.add_stats("smp", U1, stone(9))
    world.add_stats("smp", U2, stone(7))
    run_snapshot(1_000_600)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def test_total_mode_ties_and_names(env):
    j = env.get(ST + "&scope=smp").json()
    assert j["mode"] == "total" and j["total_count"] == 3
    assert [(r["rank"], r["uuid"], r["name"], r["value"]) for r in j["rows"]] == [
        (1, U1, "Alice", 9), (2, U2, "Bob", 7), (3, U4, "Dan", 5)]


def test_tie_ranks(env, world, run_snapshot):
    d = lambda n: {"minecraft:mined": {"minecraft:stone": n, "minecraft:dirt": 1 if n == 5 else 3}}
    world.add_stats("smp", U1, d(9))
    world.add_stats("smp", U2, d(7))
    world.add_stats("smp", U4, d(5))
    run_snapshot(1_001_200)
    j = env.get("/api/stats/leaderboard?kind=stat&category=minecraft:mined"
                "&key=minecraft:dirt&scope=smp").json()
    assert [(r["rank"], r["uuid"]) for r in j["rows"]] == [(1, U1), (1, U2), (3, U4)]
    j = env.get(ST + "&scope=smp&limit=1&offset=1").json()
    assert j["total_count"] == 3 and len(j["rows"]) == 1 and j["rows"][0]["rank"] == 2


def test_delta_new_player_mid_range_counts_from_zero(env):
    j = env.get(ST + "&scope=smp&from=1000300&to=1001000").json()
    assert j["mode"] == "delta"
    assert {r["uuid"]: r["value"] for r in j["rows"]} == {U1: 4, U2: 7}


def test_lifetime_not_returned_as_delta(env):
    j = env.get(ST + "&scope=smp&from=1000300&to=1001000").json()
    assert U4 not in [r["uuid"] for r in j["rows"]]


def test_range_clamped_effective_from(env):
    j = env.get(ST + "&scope=smp&range=1y&to=1001000").json()
    assert j["effective_from"] == 1_000_000 and j["mode"] == "delta"


def test_empty_scope_returns_empty_rows(env):
    r = env.get("/api/stats/leaderboard?scope=cmp2&kind=scoreboard&key=x")
    assert r.status_code == 200 and r.json()["rows"] == []


def test_leaderboard_hides_smp_bot_default_and_include_bots(env):
    uuids = lambda q: [r["uuid"] for r in env.get(ST + "&scope=smp" + q).json()["rows"]]
    assert U3 not in uuids("")
    assert uuids("&include_bots=true")[0] == U3


def test_bad_input_400(env):
    assert env.get("/api/stats/leaderboard?kind=nope&key=x").status_code == 400
    assert env.get(ST + "&range=2d").status_code == 400
    assert env.get(ST + "&scope=nope").status_code == 400
