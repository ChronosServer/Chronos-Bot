"""Carpet fake players (offline-mode, version-3 uuids) are bots on every server."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app
from api.scope import is_fake_uuid

FAKE = "aaaaaaaa-aaaa-3aaa-8aaa-aaaaaaaaaaaa"
REAL = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LB = "/api/stats/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


def q(world, sql, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("cmp", {FAKE: "Fakey", REAL: "Realo"})
    world.add_stats("cmp", FAKE, stone(50))
    world.add_stats("cmp", REAL, stone(10))
    run_snapshot(1_000_000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


def test_is_fake_uuid():
    assert is_fake_uuid(FAKE)
    assert not is_fake_uuid(REAL)
    assert not is_fake_uuid("u1")
    assert not is_fake_uuid(None)


def test_cmp_leaderboard_hides_fake_unless_include_bots(env):
    rows = env.get(LB + "&scope=cmp").json()["rows"]
    assert [r["uuid"] for r in rows] == [REAL]
    rows = env.get(LB + "&scope=cmp&include_bots=true").json()["rows"]
    assert [r["uuid"] for r in rows] == [FAKE, REAL]


def test_cmp_total_no_bots_excludes_fake(env):
    rows = q(env.world, "SELECT total, total_no_bots FROM stat_totals_current "
             "WHERE server_key='cmp' AND stat_key='minecraft:stone'")
    assert rows == [(60, 10)]


def test_fake_rule_migration_reseeds_firsts(env, run_snapshot):
    w = env.world
    c = sqlite3.connect(w.db_path)
    c.execute("UPDATE firsts SET uuid=? WHERE server_key='cmp' AND stat_key='minecraft:stone'",
              (FAKE,))
    c.execute("DELETE FROM meta WHERE key='fake_rule_applied'")
    c.commit()
    c.close()
    run_snapshot(1_000_600)
    assert q(w, "SELECT uuid, earliest_known FROM firsts WHERE server_key='cmp' "
             "AND stat_key='minecraft:stone'") == [(REAL, 1)]
    assert q(w, "SELECT value FROM meta WHERE key='fake_rule_applied'") == [("1",)]
    before = q(w, "SELECT * FROM firsts ORDER BY id")
    run_snapshot(1_001_200)
    assert q(w, "SELECT * FROM firsts ORDER BY id") == before


def test_meta_has_bots(env):
    servers = {s["key"]: s for s in env.get("/api/stats/meta").json()["servers"]}
    assert servers["cmp"]["has_bots"] is True
    assert servers["cmp2"]["has_bots"] is False
    assert servers["smp"]["has_bots"] is True  # filters_bots (team rule)
    assert servers["cmp"]["filters_bots"] is False


def test_player_fake_flags(env):
    j = env.get(f"/api/stats/player/{FAKE}").json()
    assert (j["is_fake"], j["is_bot"], j["is_bot_on_smp"]) == (True, True, False)
    j = env.get(f"/api/stats/player/{REAL}").json()
    assert (j["is_fake"], j["is_bot"]) == (False, False)


def test_compare_fake_flags(env):
    j = env.get(f"/api/stats/compare?uuids={FAKE},{REAL}"
                "&keys=minecraft:mined|minecraft:stone").json()
    assert j["players"] == [
        {"uuid": FAKE, "name": "Fakey", "is_bot_on_smp": False, "is_fake": True, "is_bot": True},
        {"uuid": REAL, "name": "Realo", "is_bot_on_smp": False, "is_fake": False, "is_bot": False}]


def test_search_hides_fake_unless_include_bots(env):
    s = "/api/stats/search"
    assert env.get(s, params={"q": "fak"}).json()["players"] == []
    assert env.get(s, params={"q": "fak", "include_bots": "true"}).json()["players"] == [
        {"uuid": FAKE, "name": "Fakey"}]
