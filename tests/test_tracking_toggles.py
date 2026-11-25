"""A server whose track_stats / track_scoreboards toggle is off is hidden at
read time (api.scope.tracked_sql), never deleted; derive is not affected."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
A = "/api/stats"


def stats(n):
    return {"minecraft:mined": {"minecraft:stone": n},
            "minecraft:custom": {"minecraft:play_time": n}}


def toggle(world, key, **flags):
    world.config["stats_tracking"]["servers"][key].update(flags)


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice"})
    world.set_usercache("cmp", {U2: "Bob"})
    world.set_scoreboard("smp", ["digs"], [("Alice", "digs", 5)], {"t": ["Alice"]})
    world.set_scoreboard("cmp", ["digs"], [("Bob", "digs", 9)], {})
    world.add_stats("smp", U1, stats(5))
    world.add_stats("cmp", U2, stats(9))
    run_snapshot(1000)
    # new keys after seeding: real (dated) firsts on both kinds
    world.set_scoreboard("cmp", ["digs", "kills"], [("Bob", "digs", 9), ("Bob", "kills", 1)], {})
    world.add_stats("cmp", U2, {**stats(9), "minecraft:crafted": {"minecraft:stick": 1}})
    run_snapshot(2000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world, c.run = world, run_snapshot
        yield c
    cache.clear()


def lb(c, qs):
    return {r["uuid"]: r["value"] for r in c.get(f"{A}/leaderboard?{qs}").json()["rows"]}


def firsts(c):
    return {(r["server_key"], r["category"], r["key"])
            for r in c.get(f"{A}/firsts").json()["rows"]}


def totals_db(world):
    con = sqlite3.connect(world.db_path)
    try:
        return con.execute("SELECT server_key, objective, at, total FROM scoreboard_totals "
                           "ORDER BY 1, 2, 3").fetchall()
    finally:
        con.close()


def test_scoreboards_off_hides_only_scoreboards(env):
    c = env
    assert lb(c, "kind=scoreboard&key=digs&scope=all") == {U1: 5, U2: 9}
    assert ("cmp", "scoreboard", "kills") in firsts(c)
    before_totals = totals_db(c.world)

    toggle(c.world, "cmp", track_scoreboards=False)
    c.run(3000)
    assert lb(c, "kind=scoreboard&key=digs&scope=all") == {U1: 5}
    assert lb(c, "kind=stat&category=minecraft:mined&key=minecraft:stone&scope=all") == {
        U1: 5, U2: 9}  # stats still shown
    f = firsts(c)
    assert ("cmp", "scoreboard", "kills") not in f
    assert ("cmp", "minecraft:crafted", "minecraft:stick") in f
    assert c.get(f"{A}/search?q=kills").json()["objectives"] == []
    assert c.get(f"{A}/search?q=digs").json()["objectives"] == [
        {"objective": "digs", "servers": ["smp"]}]
    assert c.get(f"{A}/keys?scope=cmp&kind=scoreboard").json()["objectives"] == []
    p = c.get(f"{A}/player/{U2}").json()
    assert p["scoreboards"] == [] and p["servers"] == ["cmp"]
    assert all(x["category"] != "scoreboard" for x in p["firsts"] + p["milestones"])
    t = c.get(f"{A}/totals?kind=scoreboard&key=digs&scopes=cmp,smp").json()["series"]
    assert t["cmp"] == [] and t["smp"]
    # derive kept computing on the frozen rows: no drop to 0 in totals history
    assert totals_db(c.world) == before_totals

    toggle(c.world, "cmp", track_scoreboards=True)  # back on: everything returns
    c.run(4000)
    assert lb(c, "kind=scoreboard&key=digs&scope=all") == {U1: 5, U2: 9}
    assert ("cmp", "scoreboard", "kills") in firsts(c)


def test_stats_off_hides_stats_keeps_scoreboards(env):
    c = env
    toggle(c.world, "cmp", track_stats=False)
    c.run(3000)
    assert lb(c, "kind=stat&category=minecraft:mined&key=minecraft:stone&scope=all") == {U1: 5}
    assert lb(c, "kind=scoreboard&key=digs&scope=cmps") == {U2: 9}
    f = firsts(c)
    assert ("cmp", "minecraft:crafted", "minecraft:stick") not in f
    assert ("cmp", "scoreboard", "kills") in f
    assert c.get(f"{A}/search?q=stick").json()["stats"] == []
    assert "minecraft:stick" not in str(c.get(f"{A}/keys?scope=cmp&kind=stat").json())
    p = c.get(f"{A}/player/{U2}").json()
    assert p["stats"] == [] and p["scoreboards"] and p["servers"] == ["cmp"]
    t = c.get(f"{A}/totals?kind=stat&category=minecraft:mined&key=minecraft:stone"
              f"&scopes=cmp,smp").json()["series"]
    assert t["cmp"] == [] and t["smp"]


def test_both_off_player_lists_no_servers(env):
    c = env
    toggle(c.world, "cmp", track_stats=False, track_scoreboards=False)
    c.run(3000)
    p = c.get(f"{A}/player/{U2}").json()
    assert p["servers"] == [] and p["stats"] == [] and p["scoreboards"] == []
    assert "minecraft:crafted" not in c.get(f"{A}/meta").json()["categories"]
