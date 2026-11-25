"""/rankings: players ranked by #1 spots, real firsts or badges."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "aaaaaaaa-0000-4000-8000-000000000002"
C = "aaaaaaaa-0000-4000-8000-000000000003"
FAKE = "aaaaaaaa-0000-3000-8000-000000000004"
R = "/api/stats/rankings"


def mined(**kw):
    return {"minecraft:mined": {f"minecraft:{k}": v for k, v in kw.items()}}


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("cmp", {A: "Anna", B: "Ben", C: "Cleo", FAKE: "Fakey"})
    world.add_stats("cmp", A, mined(stone=10, dirt=10, gravel=1, sand=3))
    world.add_stats("cmp", B, mined(stone=5, dirt=10, gravel=9))
    world.add_stats("cmp", C, mined(stone=1))
    world.add_stats("cmp", FAKE, mined(stone=100))
    world.set_scoreboard("cmp", ["kills", "m-stone"],
                         [("Cleo", "kills", 5), ("Anna", "kills", 1), ("Ben", "m-stone", 99)], {})
    run_snapshot(1_000_000)
    c = sqlite3.connect(world.db_path)
    c.execute("DELETE FROM firsts")
    c.execute("DELETE FROM badges")
    c.executemany(
        "INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known, "
        "approximate) VALUES (1, ?, ?, 'minecraft:mined', ?, ?, ?)",
        [("cmp", A, "minecraft:stone", 0, 0), ("cmp", A, "minecraft:dirt", 0, 1),
         ("cmp", B, "minecraft:gravel", 1, 0), ("cmp", C, "minecraft:sand", 0, 0),
         ("cmp", FAKE, "minecraft:clay", 0, 0)])
    c.executemany("INSERT INTO badges (uuid, badge_key, awarded_at, server_key) VALUES (?, ?, 1, ?)",
                  [(A, "veteran", "cmp"), (A, "veteran", "cmp2"), (A, "firsts:1", None),
                   (B, "regular", "cmp2"), (FAKE, "veteran", "cmp")])
    c.commit()
    c.close()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as cl:
        yield cl
    cache.clear()


def rows(j):
    return [(r["rank"], r["uuid"], r["value"]) for r in j["rows"]]


def test_top1(env):
    j = env.get(R, params={"by": "top1"}).json()
    # stone/dirt/sand -> Anna (dirt tied with Ben), gravel -> Ben, kills -> Cleo;
    # the mirror objective m-stone and the fake player's stone never count
    assert j["by"] == "top1" and j["total_count"] == 3
    assert rows(j) == [(1, A, 3), (2, B, 2), (3, C, 1)]
    assert j["rows"][0]["name"] == "Anna"
    assert rows(env.get(R, params={"by": "top1", "include_bots": "true"}).json()) == rows(j)


def test_firsts_count_real_ones_only(env):
    j = env.get(R, params={"by": "firsts"}).json()
    assert rows(j) == [(1, A, 2), (2, C, 1)] and j["total_count"] == 2


def test_badges_distinct_keys_and_scope(env):
    assert rows(env.get(R, params={"by": "badges"}).json()) == [(1, A, 2), (2, B, 1)]
    assert rows(env.get(R, params={"by": "badges", "scope": "cmp"}).json()) == [(1, A, 2)]


def test_ties_paging_and_bad_by(env):
    j = env.get(R, params={"by": "top1", "limit": 1, "offset": 1}).json()
    assert j["total_count"] == 3 and rows(j) == [(2, B, 2)]
    assert env.get(R, params={"by": "nope"}).status_code == 400
    assert env.get(R, params={"by": "top1", "offset": -1}).status_code == 400
