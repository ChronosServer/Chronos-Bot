"""A value of 0 is no standing: not ranked, not counted in `of`, not listed in
total-mode leaderboards, not offered by /keys or /search. And /compare says
which players are SMP bots."""
import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
BOT = "44444444-4444-4444-4444-444444444444"
A = "/api/stats"


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "Bob", U3: "Cy", BOT: "Botty"})
    world.set_scoreboard("smp", ["kills"], [("Alice", "kills", 0), ("Bob", "kills", 2)],
                         {"t": ["Alice", "Bob", "Cy"]})
    world.add_stats("smp", U1, {"minecraft:crafted": {"minecraft:air": 0},
                                "minecraft:mined": {"minecraft:stone": 5}})
    world.add_stats("smp", U2, {"minecraft:crafted": {"minecraft:air": 0},
                                "minecraft:mined": {"minecraft:stone": 0}})
    world.add_stats("smp", U3, {"minecraft:mined": {"minecraft:stone": 2}})
    world.add_stats("smp", BOT, {"minecraft:mined": {"minecraft:stone": 9}})
    run_snapshot(1_000_000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def test_player_zero_values_have_no_rank(env):
    j = env.get(f"{A}/player/{U1}?scope=smp").json()
    keys = {(s["category"], s["key"]): s for s in j["stats"]}
    assert ("minecraft:crafted", "minecraft:air") not in keys
    assert keys[("minecraft:mined", "minecraft:stone")]["rank"] == 1
    assert keys[("minecraft:mined", "minecraft:stone")]["of"] == 2   # Alice, Cy (Bob has 0)
    assert j["scoreboards"] == []                                    # kills 0
    b = env.get(f"{A}/player/{U2}?scope=smp").json()
    assert b["stats"] == [] and [s["objective"] for s in b["scoreboards"]] == ["kills"]


def test_total_leaderboard_leaves_out_zero(env):
    j = env.get(f"{A}/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone"
                f"&scope=smp").json()
    assert [r["uuid"] for r in j["rows"]] == [U1, U3] and j["total_count"] == 2
    j = env.get(f"{A}/leaderboard?kind=scoreboard&key=kills&scope=smp").json()
    assert [r["uuid"] for r in j["rows"]] == [U2]


def test_zero_only_keys_not_offered(env):
    keys = env.get(f"{A}/keys?scope=smp&kind=stat").json()["stats"]
    assert "minecraft:crafted" not in keys and keys["minecraft:mined"] == ["minecraft:stone"]
    assert env.get(f"{A}/search?q=air").json()["stats"] == []


def test_compare_players_carry_bot_flag(env):
    j = env.get(f"{A}/compare?uuids={U1},{BOT}&keys=minecraft:mined|minecraft:stone"
                f"&scope=smp").json()
    assert j["players"] == [{"uuid": U1, "name": "Alice", "is_bot_on_smp": False},
                            {"uuid": BOT, "name": "Botty", "is_bot_on_smp": True}]
