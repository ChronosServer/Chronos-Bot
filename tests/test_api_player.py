import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app
from api.rivals import closing_stats
from api.scope import resolve_scope
from api.timeq import scoreboard_values_at, stat_values_at

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-4333-3333-333333333333"
U9 = "99999999-9999-9999-9999-999999999999"
P = "/api/stats/player/"
STONE = "minecraft:mined|minecraft:stone"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


def sql(world, stmt, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        c.execute(stmt, params)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "Bob", U3: "Botty"})
    world.set_scoreboard("smp", ["kills"],
                         [("Alice", "kills", 3), ("Bob", "kills", 7), ("Botty", "kills", 50)],
                         {"t": ["Alice", "Bob"]})
    world.add_stats("smp", U1, stone(5))
    world.add_stats("smp", U2, stone(20))
    world.add_stats("smp", U3, stone(100))
    run_snapshot(1_000_000)
    world.add_stats("smp", U1, stone(9))
    run_snapshot(1_000_600)
    world.add_stats("cmp", U1, stone(2))
    run_snapshot(1_000_900)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


def stat(j, cat="minecraft:mined", key="minecraft:stone"):
    return next(s for s in j["stats"] if s["category"] == cat and s["key"] == key)


def test_player_by_uuid(env):
    j = env.get(P + U1).json()
    assert j["uuid"] == U1 and j["name"] == "Alice"
    assert j["is_bot_on_smp"] is False
    assert j["servers"] == ["smp", "cmp"]  # config order
    assert stat(j)["value"] == 11
    assert j["scoreboards"][0]["objective"] == "kills"
    assert set(j) >= {"name_resolved", "last_activity", "badges", "milestones",
                      "firsts", "rivals"}


def test_player_by_name_picks_latest_observed(env):
    sql(env.world, "INSERT INTO players (uuid, name, name_resolved, first_seen, "
        "last_observed) VALUES (?, 'alice', 0, 0, 9000000)", (U9,))
    sql(env.world, "INSERT INTO stat_current VALUES ('cmp', ?, 'minecraft:mined', "
        "'minecraft:stone', 1, 1, 1)", (U9,))  # has rows: a real profile (M4)
    assert env.get(P + "ALICE").json()["uuid"] == U9
    assert env.get(P + U1).json()["uuid"] == U1


def test_player_404(env):
    r = env.get(P + "nobody")
    assert r.status_code == 404 and r.json() == {"error": "player not found"}
    assert env.get(P + "00000000-0000-0000-0000-000000000000").status_code == 404


def test_player_rank_and_of(env):
    j = env.get(P + U1).json()
    assert (stat(j)["rank"], stat(j)["of"]) == (2, 2)
    assert (j["scoreboards"][0]["rank"], j["scoreboards"][0]["of"]) == (2, 2)
    j = env.get(P + U1 + "?include_bots=true").json()
    assert (stat(j)["rank"], stat(j)["of"]) == (3, 3)
    assert (j["scoreboards"][0]["rank"], j["scoreboards"][0]["of"]) == (3, 3)
    j = env.get(P + U2 + "?scope=smp").json()
    assert (stat(j)["rank"], stat(j)["of"]) == (1, 2)


def test_player_rank_excludes_other_bots_on_smp(env):
    j = env.get(P + U3).json()
    assert j["is_bot_on_smp"] is True
    # own row always shown; other bots excluded, the bot itself counts in `of`
    assert (stat(j)["value"], stat(j)["rank"], stat(j)["of"]) == (100, 1, 3)
    j = env.get(P + U1 + "?scope=smp").json()
    assert (stat(j)["rank"], stat(j)["of"]) == (2, 2)


def test_player_ties_share_rank(env):
    sql(env.world, "UPDATE stat_current SET value=9 WHERE uuid=? AND server_key='smp'", (U2,))
    cache.clear()
    j = env.get(P + U1 + "?scope=smp").json()
    assert (stat(j)["rank"], stat(j)["of"]) == (1, 2)


def test_player_badges_milestones_firsts_rivals(env):
    w = env.world
    sql(w, "INSERT INTO badges (uuid, badge_key, awarded_at, server_key) VALUES (?, 'b1', 5, 'smp')", (U1,))
    sql(w, "INSERT INTO badges (uuid, badge_key, awarded_at, server_key) VALUES (?, 'b2', 6, 'cmp')", (U1,))
    for i in range(55):
        sql(w, "INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key, "
            "threshold, value_at, earliest_known) VALUES (?, 'smp', ?, 'r', 'c', 'k', ?, 1, 1)",
            (i, U1, i))
    sql(w, "INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known) "
        "VALUES (1, 'smp', ?, 'c', 'k', 1)", (U1,))
    sql(w, "INSERT INTO meta (key, value) VALUES ('tracking_started_at:smp', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(1_001_200 - 86400),))
    sql(w, "INSERT INTO rivalries VALUES ('smp','c','k',?,?,10,17,1001200)", (U2, U1))
    sql(w, "INSERT INTO rivalries VALUES ('cmp','c','k',?,?,10,17,1001200)", (U2, U1))
    cache.clear()
    j = env.get(P + U1 + "?scope=smp").json()
    assert [b["badge_key"] for b in j["badges"]] == ["b1"]
    assert len(j["milestones"]) == 50 and j["milestones"][0]["at"] == 54
    assert j["milestones"][0]["earliest_known"] == 1
    assert j["firsts"][0]["earliest_known"] == 1
    assert len(j["rivals"]) == 1
    r = j["rivals"][0]
    assert r["other_uuid"] == U2 and r["other_name"] == "Bob"
    assert r["closing_per_day"] == pytest.approx(7.0)
    assert r["overtake_days"] == pytest.approx(10 / 7)
    assert env.get(P + U2 + "?scope=smp").json()["rivals"][0]["other_uuid"] == U1


def test_closing_stats_window_clamped_to_tracking_start():
    c, o = closing_stats(10, 17, 1_000_000, 1_000_000 - 86400)
    assert c == pytest.approx(7) and o == pytest.approx(10 / 7)
    c, o = closing_stats(10, 17, 1_000_000, 1_000_000 - 30 * 86400)  # full 7d window
    assert c == pytest.approx(1.0)
    c, o = closing_stats(10, 17, 1_000_000, 1_000_000)  # min window 1/96 day
    assert c == pytest.approx(7 * 96)
    assert closing_stats(10, 5, 1_000_000, 0) == (-5 / 7, None)
    assert closing_stats(10, None, 1_000_000, 0) == (None, None)
    c, o = closing_stats(10, 17, 1_000_000, None)
    assert c == pytest.approx(1.0)


C = "/api/stats/compare?"


def test_compare_limits(env):
    assert env.get(C + f"uuids={U1}&keys={STONE}").status_code == 400
    assert env.get(C + f"uuids={U1},{U1}&keys={STONE}").status_code == 400
    ids = ",".join(f"{i}" * 8 for i in range(1, 8))
    assert env.get(C + f"uuids={ids}&keys={STONE}").status_code == 400
    assert env.get(C + f"uuids={U1},nobody&keys={STONE}").status_code == 404
    assert env.get(C + f"uuids={U1},{U2}&keys=").status_code == 400
    assert env.get(C + f"uuids={U1},{U2}&keys=nokey").status_code == 400


def test_compare_values(env):
    j = env.get(C + f"uuids={U1},bob&keys={STONE},scoreboard|kills&scope=smp").json()
    assert j["players"] == [{"uuid": U1, "name": "Alice", "is_bot_on_smp": False},
                            {"uuid": U2, "name": "Bob", "is_bot_on_smp": False}]
    assert j["values"][STONE] == {U1: 9, U2: 20}
    assert j["values"]["scoreboard|kills"] == {U1: 3, U2: 7}
    assert j["series"][STONE][U1][-1] == [1_000_600, 9]
    assert j["series"]["scoreboard|kills"][U2][-1][1] == 7
    j = env.get(C + f"uuids={U1},{U2}&keys={STONE}").json()
    assert j["values"][STONE][U1] == 11


def test_compare_delta_with_range(env):
    j = env.get(C + f"uuids={U1},{U2}&keys={STONE}&scope=smp&from=1000100&to=1001000").json()
    assert j["values"][STONE] == {U1: 4, U2: 0}


def test_stat_current_key_indexes_used(env):
    c = sqlite3.connect(env.world.db_path)
    plans = []

    class Spy:
        def execute(self, s, p=()):
            plans.append(" | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + s, p)))
            return c.execute(s, p)
    try:
        sc = resolve_scope(c, "all")
        stat_values_at(Spy(), sc, False, "minecraft:mined", "minecraft:stone", None)
        scoreboard_values_at(Spy(), sc, False, "kills", None)
    finally:
        c.close()
    assert "idx_stat_current_key" in plans[0], plans[0]
    assert "idx_scoreboard_current_obj" in plans[1], plans[1]
    assert "category=?" in plans[0] and "stat_key=?" in plans[0]
    assert "objective=?" in plans[1]


def test_player_rank_sums_across_servers(env):
    sql(env.world, "UPDATE stat_current SET value=50 WHERE uuid=? AND server_key='cmp'", (U1,))
    cache.clear()
    assert (stat(env.get(P + U1 + "?scope=smp").json())["rank"]) == 2
    j = env.get(P + U1).json()
    assert (stat(j)["value"], stat(j)["rank"], stat(j)["of"]) == (59, 1, 2)


def test_is_bot_on_smp_ignores_scope(env):
    assert env.get(P + U3 + "?scope=cmp").json()["is_bot_on_smp"] is True
    assert env.get(P + U1 + "?scope=cmp").json()["is_bot_on_smp"] is False


def test_compare_keys_cap_and_bad_inputs(env):
    ok = ",".join(f"minecraft:mined|k{i}" for i in range(20))
    assert env.get(C + f"uuids={U1},{U2}&keys={ok}").status_code == 200
    many = ok + ",minecraft:mined|k20"
    assert env.get(C + f"uuids={U1},{U2}&keys={many}").status_code == 400
    assert env.get(C + f"uuids={U1},{U2}&keys={STONE}&scope=nope").status_code == 400
    assert env.get(C + f"uuids={U1},{U2}&keys={STONE}&range=2d").status_code == 400


def test_values_at_uuid_filter(env):
    c = sqlite3.connect(env.world.db_path)
    try:
        sc = resolve_scope(c, "smp")
        for ts in (None, 1_000_700):
            assert stat_values_at(c, sc, True, "minecraft:mined", "minecraft:stone",
                                  ts, [U1]) == {U1: 9}
            assert stat_values_at(c, sc, True, "minecraft:mined", "minecraft:stone", ts,
                                  []) == {}
            assert scoreboard_values_at(c, sc, True, "kills", ts, [U2, U3]) == {U2: 7, U3: 50}
    finally:
        c.close()


def test_startup_fails_when_required_index_missing(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice"})
    world.add_stats("smp", U1, stone(1))
    run_snapshot(1_000_000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    with TestClient(create_app()):
        pass  # all indexes present: starts fine
    sql(world, "DROP INDEX idx_stat_current_key")
    with pytest.raises(RuntimeError, match=r"run bot/snapshot.py.*idx_stat_current_key"):
        with TestClient(create_app()):
            pass


def test_player_without_any_rows_is_404(env):
    sql(env.world, "INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
        "VALUES (?, 'Ghost', 1, 1, 1)", (U9,))
    cache.clear()
    assert env.get(P + U9).status_code == 404
    r = env.get(P + "ghost")
    assert r.status_code == 404 and r.json() == {"error": "player not found"}
    assert env.get(P + U3).status_code == 200  # an SMP bot with rows keeps a profile


def test_player_rivals_reapply_bot_rule_to_other(env):
    sql(env.world, "INSERT INTO rivalries VALUES ('smp','c','k',?,?,10,17,1001200)", (U1, U3))
    sql(env.world, "INSERT INTO rivalries VALUES ('smp','c','k2',?,?,10,17,1001200)", (U2, U1))
    cache.clear()
    j = env.get(P + U1 + "?scope=smp").json()
    assert [r["other_uuid"] for r in j["rivals"]] == [U2]
    j = env.get(P + U1 + "?scope=smp&include_bots=true").json()
    assert sorted(r["other_uuid"] for r in j["rivals"]) == sorted([U2, U3])


def test_compare_total_mode_with_to_only(env):
    j = env.get(C + f"uuids={U1},{U2}&keys={STONE}&scope=smp&to=1000300").json()
    assert j["values"][STONE] == {U1: 5, U2: 20}
