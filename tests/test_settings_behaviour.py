"""Each stats_tracking tuning block actually changes behaviour."""
import json
import sqlite3

import derive
import pytest
import snapshot
from fastapi.testclient import TestClient

from api import cache
from api.scope import Scope, resolve_scope, stat_filter_sql
from api.settings import load_settings

P = "aaaaaaaa-0000-0000-0000-000000000001"
P2 = "aaaaaaaa-0000-0000-0000-000000000002"
BOT = "bbbbbbbb-0000-0000-0000-000000000003"
DAY = 86400
RUN = 100 * DAY
T0 = RUN - 10 * DAY


@pytest.fixture
def db(world):
    c = sqlite3.connect(world.db_path)
    yield c
    c.close()


def q(db, sql, *a):
    return db.execute(sql, a).fetchall()


def tracking(world):
    return world.config["stats_tracking"]


def counted(db, server):
    frag, params = stat_filter_sql(Scope(server, (server,)), False, "s")
    return {r[0] for r in db.execute(f"SELECT DISTINCT s.uuid FROM stat_current s WHERE {frag}",
                                     params)}


def mined(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


# ------------------------------------------------------------ servers.filters_bots

def test_filters_bots_false_on_smp_disables_filtering_and_team_check(world, run_snapshot, db):
    tracking(world)["servers"]["smp"]["filters_bots"] = False
    world.set_usercache("smp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    world.add_stats("smp", P, mined(5))
    world.add_stats("smp", BOT, mined(9))
    run_snapshot(1000)
    assert q(db, "SELECT filters_bots FROM servers WHERE key='smp'") == [(0,)]
    assert q(db, "SELECT * FROM snapshot_runs WHERE kind='team_check'") == []
    assert q(db, "SELECT * FROM player_flags") == []
    assert counted(db, "smp") == {P, BOT}


def test_filters_bots_true_on_cmp_enables_filtering_and_team_check(world, run_snapshot, db):
    tracking(world)["servers"]["cmp"]["filters_bots"] = True
    world.set_usercache("cmp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("cmp", [], [], {"t": ["Alice"]})
    world.add_stats("cmp", P, mined(5))
    world.add_stats("cmp", BOT, mined(9))
    run_snapshot(1000)
    assert q(db, "SELECT key, filters_bots FROM servers WHERE key IN ('cmp','cmp2','smp') "
                 "ORDER BY key") == [("cmp", 1), ("cmp2", 0), ("smp", 1)]
    assert q(db, "SELECT server_key, ok FROM snapshot_runs WHERE kind='team_check' "
                 "ORDER BY server_key") == [("cmp", 1), ("smp", 1)]
    assert dict(q(db, "SELECT uuid, is_bot FROM player_flags WHERE server_key='cmp'")) == {
        P: 0, BOT: 1}
    assert counted(db, "cmp") == {P}


# ------------------------------------------------------------ scope_groups

def test_custom_scope_group_resolves(world, run_snapshot, db, api_config, monkeypatch):
    api_config({"scope_groups": {"creative": "cmp"}})
    world.add_stats("cmp", P, mined(5))
    world.add_stats("cmp2", P2, mined(7))
    run_snapshot(1000)
    assert resolve_scope(db, "creative").servers == ("cmp", "cmp2")
    with pytest.raises(ValueError):
        resolve_scope(db, "cmps")
    from api.main import create_app
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    with TestClient(create_app()) as c:
        scopes = c.get("/api/stats/meta").json()["scopes"]
        assert scopes == ["all", "smp", "creative", "cmp", "cmp2"]
        j = c.get("/api/stats/leaderboard?kind=stat&category=minecraft:mined"
                  "&key=minecraft:stone&scope=creative").json()
        assert [r["uuid"] for r in j["rows"]] == [P2, P]
        assert c.get("/api/stats/leaderboard?kind=stat&category=minecraft:mined"
                     "&key=minecraft:stone&scope=cmps").status_code == 400


# ------------------------------------------------------------ activity_stat

def test_activity_stat_override(world, run_snapshot, db):
    tracking(world)["activity_stat"] = ["minecraft:custom", "minecraft:jump"]

    def st(pt, jump):
        return {"minecraft:custom": {"minecraft:play_time": pt, "minecraft:jump": jump}}
    world.add_stats("cmp", P, st(10, 1))
    run_snapshot(1000)
    world.add_stats("cmp", P, st(20, 1))   # play time only: not activity now
    run_snapshot(1900)
    world.add_stats("cmp", P, st(20, 2))
    run_snapshot(2800)
    assert q(db, "SELECT server_key, at, uuid FROM activity") == [("cmp", 2800, P)]


# ------------------------------------------------------------ milestones

def rules(db):
    return {r[0]: (json.loads(r[1]), r[2]) for r in q(
        db, "SELECT rule_key, thresholds, enabled FROM milestone_rules")}


def test_milestone_rule_sync_updates_and_disables(world, run_snapshot, db):
    deaths = lambda n: {"minecraft:custom": {"minecraft:deaths": n}}  # noqa: E731
    world.add_stats("cmp", P, deaths(150))
    run_snapshot(1000)
    assert rules(db)["key:minecraft:custom/minecraft:deaths"] == ([100, 1000, 10000], 1)
    seeded = q(db, "SELECT rule_key, threshold, earliest_known FROM milestones")
    assert seeded == [("key:minecraft:custom/minecraft:deaths", 100, 1)]

    tracking(world)["milestones"] = {
        "count_categories": ["minecraft:mined"],
        "per_key": [{"category": "minecraft:custom", "key": "minecraft:deaths",
                     "thresholds": [5, 200], "scale": 1}],
    }
    world.add_stats("cmp", P, deaths(250))
    run_snapshot(1900)
    r = rules(db)
    assert len(r) == 11  # nothing deleted
    assert r["key:minecraft:custom/minecraft:deaths"] == ([5, 200], 1)
    assert r["cat:minecraft:mined"][1] == 1
    assert r["cat:minecraft:used"][1] == 0
    assert r["key:minecraft:custom/minecraft:play_time"][1] == 0
    # old rows kept; the new 200 crossing recorded; 5 is not backfilled
    assert q(db, "SELECT threshold, earliest_known, at FROM milestones ORDER BY at") == [
        (100, 1, 1000), (200, 0, 1900)]

    # a rule coming back is re-enabled
    del tracking(world)["milestones"]
    run_snapshot(2800)
    assert rules(db)["cat:minecraft:used"] == ([1000, 10000, 100000, 1000000, 10000000], 1)


# ------------------------------------------------------------ badges

def test_badges_veteran_hours_override(world, run_snapshot, db):
    tracking(world)["badges"] = {"veteran_hours": 2}
    pt = lambda n: {"minecraft:custom": {"minecraft:play_time": n}}  # noqa: E731
    world.add_stats("cmp", P, pt(2 * 72000))
    world.add_stats("cmp", P2, pt(2 * 72000 - 1))
    run_snapshot(1000)
    assert q(db, "SELECT uuid FROM badges WHERE badge_key='veteran'") == [(P,)]


def test_badges_firsts_tiers_change_badge_keys(world, run_snapshot, db):
    tracking(world)["badges"] = {"firsts": [2]}
    world.add_stats("cmp", P, mined(1))
    run_snapshot(1000)
    run_snapshot(1900)
    world.add_stats("cmp", P, {"minecraft:mined": {"minecraft:stone": 1, "minecraft:dirt": 1,
                                                   "minecraft:sand": 1}})
    run_snapshot(2800)
    assert q(db, "SELECT badge_key FROM badges WHERE badge_key LIKE 'firsts:%'") == [
        ("firsts:2",)]


# ------------------------------------------------------------ rivalries / spotlight

def _put(db, server, uuid, cat, key, value, at):
    db.execute("INSERT OR IGNORE INTO players (uuid, name, name_resolved, first_seen, "
               "last_observed) VALUES (?, NULL, 0, ?, ?)", (uuid, at, at))
    db.execute("INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
               "VALUES (?, ?, ?, ?, ?, ?)", (at, server, uuid, cat, key, value))
    db.execute("INSERT INTO stat_current (server_key, uuid, category, stat_key, value, "
               "updated_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(server_key, uuid, category, stat_key) "
               "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
               (server, uuid, cat, key, value, at, at))


def _fresh(run_snapshot, db):
    run_snapshot(50)
    for t in ("firsts", "milestones", "badges", "rivalries", "spotlight_candidates",
              "activity", "stat_current", "stat_snapshots", "snapshot_runs", "players"):
        db.execute(f"DELETE FROM {t}")
    db.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, "
               "rows_written) VALUES (?, 'cmp', 'stats', 1, 1, 1)", (T0,))


def test_rivalry_max_gap_ratio_override(run_snapshot, db):
    _fresh(run_snapshot, db)
    _put(db, "cmp", P, "minecraft:mined", "wide", 10000, T0)
    _put(db, "cmp", P2, "minecraft:mined", "wide", 9000, T0)
    _put(db, "cmp", P2, "minecraft:mined", "wide", 9700, RUN - DAY)  # gap 300 = 3%
    for u in (P, P2):
        db.execute("INSERT INTO activity VALUES ('cmp', ?, ?)", (RUN - DAY, u))
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert q(db, "SELECT * FROM rivalries") == []
    derive.derive_rivalries(db, RUN, load_settings({"rivalries": {"max_gap_ratio": 0.05}}))
    assert q(db, "SELECT uuid_a, uuid_b, gap FROM rivalries") == [(P, P2, 300)]


def test_spotlight_rare_max_holders_override(run_snapshot, db):
    _fresh(run_snapshot, db)
    for i, u in enumerate((P, P2, BOT, "cccccccc-0000-0000-0000-000000000009")):
        _put(db, "cmp", u, "minecraft:killed", "ghast", i + 1, T0)
    db.commit()
    derive.derive_spotlight(db, RUN)
    assert q(db, "SELECT * FROM spotlight_candidates WHERE trigger='rare'") == []
    derive.derive_spotlight(db, RUN, load_settings({"spotlight": {"rare": {"max_holders": 4}}}))
    assert q(db, "SELECT stat_key FROM spotlight_candidates WHERE trigger='rare'") == [
        ("ghast",)]


# ------------------------------------------------------------ mojang

def test_mojang_max_calls_respected(world, run_snapshot):
    tracking(world)["mojang"] = {"max_calls": 1}
    for i in range(3):
        world.add_stats("cmp", f"cccccccc-0000-0000-0000-00000000000{i}", mined(1))
    calls = []
    run_snapshot(1000, mojang_fetch=lambda m, u, b: (calls.append(u) or (404, b"")))
    assert len(calls) == 1


def test_mojang_timeout_and_retry_after(world, run_snapshot, monkeypatch):
    import mojang
    tracking(world)["mojang"] = {"timeout_s": 1.5, "retry_after_days": 1}
    timeouts = []

    def fake_urlopen(req, timeout=None):
        timeouts.append(timeout)
        raise OSError("offline")
    monkeypatch.setattr(mojang.urllib.request, "urlopen", fake_urlopen)
    world.add_stats("cmp", P, mined(1))
    run_snapshot(1000, mojang_fetch=None)
    run_snapshot(1000 + DAY // 2, mojang_fetch=None)   # cached for 1 day
    assert timeouts == [1.5]
    run_snapshot(1000 + 2 * DAY, mojang_fetch=None)    # default 7 days would still skip
    assert timeouts == [1.5, 1.5]


# ------------------------------------------------------------ snapshot

def test_snapshot_dictionary_cache_and_timeout(tmp_path, monkeypatch):
    conn = snapshot.connect_db(str(tmp_path / "d.db"))
    timeouts = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"a": "minecraft.custom:minecraft.jump"}'

    def fake_urlopen(url, timeout=None):
        timeouts.append(timeout)
        return Resp()
    monkeypatch.setattr(snapshot.urllib.request, "urlopen", fake_urlopen)
    s = load_settings({"snapshot": {"dictionary_cache_hours": 0, "dictionary_timeout_s": 4}})
    snapshot.fetch_dictionary(conn, "v", s)
    snapshot.fetch_dictionary(conn, "v", s)
    assert timeouts == [4, 4]
    snapshot.fetch_dictionary(conn, "v")   # defaults: 24 h cache -> no fetch
    assert timeouts == [4, 4]
    conn.close()


# ------------------------------------------------------------ api

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-4333-3333-333333333333"


@pytest.fixture
def api_env(world, run_snapshot, monkeypatch, api_config):
    def st(stone, pt):
        return {"minecraft:mined": {"minecraft:stone": stone},
                "minecraft:custom": {"minecraft:play_time": pt}}
    world.set_usercache("cmp", {U1: "Alice", U2: "Bob", U3: "Carl"})
    world.add_stats("cmp", U1, st(5000, 1))
    world.add_stats("cmp", U2, st(3000, 1))
    world.add_stats("cmp", U3, st(2000, 1))
    run_snapshot(1_000_000)
    world.add_stats("cmp", U1, st(6000, 2))
    run_snapshot(1_000_900)
    world.add_stats("cmp", U1, st(7000, 3))
    run_snapshot(1_001_800)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)

    def make(api):
        api_config({"api": api})
        cache.clear()
        from api.main import create_app
        return TestClient(create_app())
    yield make
    cache.clear()


LB = "/api/stats/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone&scope=cmp"


def test_api_limits_override(api_env):
    c = api_env({"leaderboard_max_limit": 1, "feed_max_limit": 1, "search_max_limit": 1,
                 "compare_max_players": 2, "compare_max_keys": 1, "series_max_players": 1,
                 "player_milestones_limit": 1})
    assert len(c.get(LB + "&limit=50").json()["rows"]) == 1
    j = c.get("/api/stats/milestones?limit=50").json()
    assert len(j["rows"]) == 1 and j["next"] is not None
    assert len(c.get("/api/stats/search?q=a&limit=10").json()["players"]) == 1
    assert c.get(f"/api/stats/series?kind=stat&category=minecraft:mined&key=minecraft:stone"
                 f"&uuids={U1},{U2}").status_code == 400
    keys = "minecraft:mined|minecraft:stone"
    assert c.get(f"/api/stats/compare?uuids={U1},{U2},{U3}&keys={keys}").status_code == 400
    assert c.get(f"/api/stats/compare?uuids={U1},{U2}&keys={keys}").status_code == 200
    assert c.get(f"/api/stats/compare?uuids={U1},{U2}&keys={keys},"
                 "minecraft:custom|minecraft:play_time").status_code == 400
    assert len(c.get(f"/api/stats/player/{U1}").json()["milestones"]) == 1


def test_api_range_presets_bucket_and_series_points(api_env):
    c = api_env({"range_presets": {"2w": 1209600}, "activity_bucket_s": 3600,
                 "series_max_points": 2})
    assert c.get(LB + "&range=2w").status_code == 200
    assert c.get(LB + "&range=1d").status_code == 400
    a = c.get("/api/stats/activity?scope=cmp&range=2w").json()
    assert a["bucket_seconds"] == 3600
    assert all(b % 3600 == 0 for b, _ in a["buckets"])
    s = c.get(f"/api/stats/series?kind=stat&category=minecraft:mined&key=minecraft:stone"
              f"&uuids={U1}&from=1000000&to=1002000").json()
    assert len(s["series"][U1]) == 2
    m = c.get("/api/stats/meta").json()["settings"]
    assert m["range_presets"] == {"2w": 1209600}
    assert m["activity_bucket_s"] == 3600
    assert m["limits"]["series_points"] == 2


def test_parse_range_uses_presets_argument(db, world, run_snapshot):
    from api import timeq
    run_snapshot(1000)
    sc = Scope("cmp", ("cmp",))
    r = timeq.parse_range(db, sc, "2w", None, None, 5_000_000, presets={"2w": 1209600})
    assert r.from_ts == 5_000_000 - 1209600
    with pytest.raises(ValueError):
        timeq.parse_range(db, sc, "1w", None, None, 5_000_000, presets={"2w": 1209600})


def test_api_cache_settings(api_env):
    c = api_env({"cache_max_entries": 1, "cache_max_age_s": 5,
                 "cache_stale_while_revalidate_s": 10})
    r = c.get(LB)
    assert r.headers["Cache-Control"] == "public, max-age=5, stale-while-revalidate=10"
    c.get(LB + "&limit=3")
    assert len(cache._lru) == 1


def test_etag_changes_with_settings(api_env):
    e1 = api_env({}).get(LB).headers["ETag"]
    e2 = api_env({"leaderboard_max_limit": 5}).get(LB, headers={"If-None-Match": e1})
    assert e2.status_code == 200 and e2.headers["ETag"] != e1


def test_meta_settings_defaults(api_env):
    c = api_env({})
    m = c.get("/api/stats/meta").json()["settings"]
    assert m == {
        "range_presets": {"1d": 86400, "1w": 604800, "1m": 2592000, "6m": 15724800,
                          "1y": 31536000},
        "activity_bucket_s": 900,
        "limits": {"leaderboard": 200, "feed": 200, "search": 50, "series_players": 6,
                   "series_points": 500, "player_milestones": 50},
        "compare": {"min_players": 2, "max_players": 6, "max_keys": 20},
        "badges": {"firsts": [1, 10, 50], "top1": [1, 10, 50],
                   "big_numbers": [1000000, 10000000],
                   "category_tiers": [100000, 1000000, 10000000],
                   "categories": ["minecraft:mined", "minecraft:used", "minecraft:crafted",
                                  "minecraft:broken", "minecraft:picked_up",
                                  "minecraft:dropped", "minecraft:killed",
                                  "minecraft:killed_by"],
                   "veteran_hours": 1000,
                   "regular": {"days": 20, "window_days": 30},
                   "night_owl": {"min_buckets": 40, "window_days": 30, "share": 0.5,
                                 "start_hour": 0, "end_hour": 6}},
    }
