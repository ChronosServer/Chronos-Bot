import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
STONE = {"minecraft:mined": {"minecraft:stone": 1}}
NOW = int(time.time())


def sql(world, stmt, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        c.execute(stmt, params)
        c.commit()
    finally:
        c.close()


def many(world, stmt, rows):
    c = sqlite3.connect(world.db_path)
    try:
        c.executemany(stmt, rows)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "Bob", U3: "Botty"})
    world.set_scoreboard("smp", ["kills"],
                         [("Alice", "kills", 3), ("Bob", "kills", 7), ("Botty", "kills", 50)],
                         {"t": ["Alice", "Bob"]})
    for u in (U1, U2, U3):
        world.add_stats("smp", u, STONE)
    run_snapshot(1_000_000)
    for t in ("milestones", "firsts", "rivalries", "activity", "spotlight_candidates"):
        sql(world, f"DELETE FROM {t}")  # drop derived rows; tests insert their own
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


MS = ("INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key, "
      "threshold, value_at, earliest_known) VALUES (?, 'smp', ?, 'r', 'c', 'k', ?, 1, ?)")


def add_ms(world, rows):
    many(world, MS, rows)


def test_milestones_pagination(env):
    add_ms(env.world, [(100 + i, U1, i, 0) for i in range(5)])
    j = env.get("/api/stats/milestones?limit=2").json()
    assert [r["threshold"] for r in j["rows"]] == [4, 3]
    assert j["rows"][0]["name"] == "Alice" and j["rows"][0]["key"] == "k"
    assert j["next"]
    j2 = env.get("/api/stats/milestones?limit=2&before=" + j["next"]).json()
    assert [r["threshold"] for r in j2["rows"]] == [2, 1]
    j3 = env.get("/api/stats/milestones?limit=2&before=" + j2["next"]).json()
    assert [r["threshold"] for r in j3["rows"]] == [0] and j3["next"] is None
    assert env.get("/api/stats/milestones?before=junk").status_code == 400


def test_milestones_earliest_known_last(env):
    add_ms(env.world, [(500, U1, 1, 1), (100, U1, 2, 0), (200, U2, 3, 0), (900, U2, 4, 1)])
    r = env.get("/api/stats/milestones?limit=3").json()
    assert [x["threshold"] for x in r["rows"]] == [3, 2, 4]
    r2 = env.get("/api/stats/milestones?limit=3&before=" + r["next"]).json()
    assert [x["threshold"] for x in r2["rows"]] == [1] and r2["next"] is None


def test_milestones_bot_rule(env):
    add_ms(env.world, [(100, U3, 1, 0), (101, U1, 2, 0)])
    assert [r["uuid"] for r in env.get("/api/stats/milestones").json()["rows"]] == [U1]
    assert len(env.get("/api/stats/milestones?include_bots=true").json()["rows"]) == 2


def test_firsts(env):
    many(env.world, "INSERT INTO firsts (at, server_key, uuid, category, stat_key, "
         "earliest_known) VALUES (?, 'smp', ?, 'c', ?, 0)", [(5, U1, "a"), (6, U3, "b")])
    rows = env.get("/api/stats/firsts").json()["rows"]
    assert [(r["uuid"], r["name"], r["key"]) for r in rows] == [(U1, "Alice", "a")]
    assert len(env.get("/api/stats/firsts?include_bots=true").json()["rows"]) == 2


def test_rivalry_overtake_days(env):
    many(env.world, "INSERT INTO rivalries VALUES ('smp', 'c', ?, ?, ?, ?, ?, ?)",
         [("slow", U1, U2, 10, 12, NOW), ("fast", U1, U2, 10, 80, NOW),
          ("none", U1, U2, 10, 5, NOW), ("bot", U1, U3, 1, 50, NOW)])
    rows = env.get("/api/stats/rivalries").json()["rows"]
    assert [r["key"] for r in rows] == ["fast", "slow", "none"]
    f = rows[0]
    assert f["a"] == {"uuid": U1, "name": "Alice"} and f["b"]["name"] == "Bob"
    assert f["closing_per_day"] > 0 and f["overtake_days"] == pytest.approx(
        f["gap"] / f["closing_per_day"])
    assert rows[2]["overtake_days"] is None
    only = env.get("/api/stats/rivalries?uuid=" + U2 + "&limit=1").json()["rows"]
    assert len(only) == 1
    assert len(env.get("/api/stats/rivalries?uuid=" + U3).json()["rows"]) == 0


def test_activity_buckets_excludes_smp_bots(env):
    b = (NOW // 900) * 900
    many(env.world, "INSERT INTO activity (server_key, at, uuid) VALUES (?, ?, ?)",
         [("smp", b, U1), ("smp", b + 5, U2), ("smp", b, U3), ("cmp", b, U1),
          ("smp", b - 900, U1)])
    j = env.get("/api/stats/activity").json()
    assert j["bucket_seconds"] == 900
    assert j["buckets"] == [[b - 900, 1], [b, 2]]
    j = env.get("/api/stats/activity?include_bots=true").json()
    assert j["buckets"][-1] == [b, 3]
    assert env.get("/api/stats/activity?scope=smp").json()["buckets"][-1] == [b, 2]
    assert env.get("/api/stats/activity?range=bogus").status_code == 400


SP = ("INSERT INTO spotlight_candidates (trigger, server_key, uuid, category, stat_key, "
      "value, detail_json, computed_at) VALUES (?, 'smp', ?, ?, ?, 7, ?, 1)")


def test_spotlight_rotates_trigger_types(env):
    many(env.world, SP, [("outlier", U1, "c", "a", '{"x": 1}'),
                         ("outlier", U2, "c", "b", "{}"),
                         ("rare", U1, "c", "d", "{}")])
    got = set()
    for _ in range(200):
        j = env.get("/api/stats/spotlight").json()
        got.add(j["trigger"])
    assert got == {"outlier", "rare"}
    j = {"trigger": None}
    while j["trigger"] != "outlier" or j["key"] != "a":
        j = env.get("/api/stats/spotlight").json()
    assert j["name"] == "Alice" and j["detail"] == {"x": 1} and j["value"] == 7
    assert j["category"] == "c" and j["server_key"] == "smp"


def test_spotlight_respects_seen(env):
    many(env.world, SP, [("outlier", U1, "c", "a", "{}"), ("rare", U1, "c", "d", "{}"),
                         ("rare", U3, "c", "bot", "{}")])
    for _ in range(30):
        assert env.get("/api/stats/spotlight?seen=c|a").json()["key"] == "d"
    for _ in range(10):
        assert env.get("/api/stats/spotlight?seen=c|a,c|d").json() == {"trigger": None}
    for _ in range(10):
        assert env.get("/api/stats/spotlight").json()["key"] != "bot"


def test_spotlight_empty_returns_null_trigger(env):
    assert env.get("/api/stats/spotlight").json() == {"trigger": None}


def test_spotlight_not_cached(env):
    r = env.get("/api/stats/spotlight")
    assert "etag" not in r.headers


@pytest.mark.parametrize("include_bots", [False, True])
@pytest.mark.parametrize("cursor", [False, True])
def test_milestones_feed_uses_index_no_sort(env, include_bots, cursor):
    from api.routes.feeds import milestones_sql
    from api.scope import resolve_scope, stat_filter_sql
    add_ms(env.world, [(1000 + i, U1, i, 0) for i in range(50)])
    c = sqlite3.connect(env.world.db_path)
    try:
        frag, params = stat_filter_sql(resolve_scope(c, "all"), include_bots, "m")
        cur_sql, cur = "", []
        if cursor:
            cur_sql = (" AND (m.earliest_known > ? OR (m.earliest_known = ? AND "
                       "(m.at < ? OR (m.at = ? AND m.id < ?))))")
            cur = [0, 0, 1020, 1020, 21]
        plan = " | ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN " + milestones_sql(frag, cur_sql), [*params, *cur, 51]))
    finally:
        c.close()
    assert "INDEX idx_milestones_feed" in plan, plan
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan, plan


def test_milestones_feed_index_required():
    from api import db
    assert "idx_milestones_feed" in db.REQUIRED_INDEXES


def test_mirror_objective_firsts_and_milestones_hidden(env):
    """An objective stored as real before its dictionary was bundled, later
    flagged as a mirror: its scoreboard firsts/milestones (and totals) are
    hidden; stat rows with the same key name are not."""
    w = env.world
    many(w, "INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known) "
            "VALUES (?, 'smp', ?, ?, ?, 0)",
         [(100, U1, "scoreboard", "kills"), (101, U1, "minecraft:custom", "kills")])
    many(w, "INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key, "
            "threshold, value_at, earliest_known) VALUES (?, 'smp', ?, 'r', ?, ?, 1, 3, 0)",
         [(100, U1, "scoreboard", "kills"), (101, U1, "minecraft:custom", "kills")])

    def seen():
        cache.clear()
        f = [r["category"] for r in env.get("/api/stats/firsts?scope=smp").json()["rows"]]
        m = [r["category"] for r in env.get("/api/stats/milestones?scope=smp").json()["rows"]]
        p = env.get(f"/api/stats/player/{U1}?scope=smp").json()
        t = env.get("/api/stats/totals?kind=scoreboard&key=kills&scopes=smp"
                    "&from=0&to=2000000").json()["series"]["smp"]
        return (sorted(f), sorted(m), sorted(r["category"] for r in p["firsts"]),
                sorted(r["category"] for r in p["milestones"]), bool(t))

    both = ["minecraft:custom", "scoreboard"]
    assert seen() == (both, both, both, both, True)
    sql(w, "UPDATE scoreboard_meta SET is_vanilla_mirror=1 WHERE server_key='smp' "
           "AND objective='kills'")
    only = ["minecraft:custom"]
    assert seen() == (only, only, only, only, False)
