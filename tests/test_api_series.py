import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app
from api.scope import resolve_scope
from api.timeq import Range

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
U4 = "44444444-4444-4444-4444-444444444444"
S = "/api/stats/series?kind=stat&category=minecraft:mined&key=minecraft:stone"
T = "/api/stats/totals?kind=stat&category=minecraft:mined&key=minecraft:stone"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "Bob", U3: "Botty"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice", "Bob"]})
    world.add_stats("smp", U1, stone(5))
    world.add_stats("smp", U3, stone(100))
    run_snapshot(1_000_000)
    world.add_stats("smp", U1, stone(9))
    run_snapshot(1_000_600)
    world.add_stats("cmp", U1, stone(2))
    run_snapshot(1_000_900)
    world.add_stats("smp", U1, stone(12))
    world.add_stats("cmp", U1, stone(4))
    run_snapshot(1_001_200)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


def test_series_starts_at_effective_from(env):
    j = env.get(S + f"&uuids={U1}&scope=smp&from=1000700&to=1001500").json()
    assert j["effective_from"] == 1_000_700
    assert j["series"][U1] == [[1_000_700, 9], [1_001_200, 12]]


def test_series_multi_server_sum_steps(env):
    j = env.get(S + f"&uuids={U1}&scope=all&from=1000000&to=1001500").json()
    assert j["series"][U1] == [[1_000_000, 5], [1_000_600, 9], [1_000_900, 11],
                               [1_001_200, 16]]


def test_series_rejects_7_uuids(env):
    ids = ",".join(f"{i}" * 8 for i in range(1, 8))
    assert env.get(S + f"&uuids={ids}").status_code == 400
    assert env.get(S + "&uuids=").status_code == 400
    assert env.get(S + "&uuids=a,a,a,a,a,a,a").status_code == 200


def test_series_unknown_uuid_present_empty_and_bot_hidden(env):
    j = env.get(S + f"&uuids={U3},{U4}&scope=smp").json()
    assert j["series"] == {U3: [], U4: []}
    j = env.get(S + f"&uuids={U3}&scope=smp&include_bots=true").json()
    assert j["series"][U3][-1] == [1_000_000, 100]


def test_series_empty_window_and_bad_input(env):
    j = env.get(S + f"&uuids={U1}&scope=smp&from=0&to=500").json()
    assert j["series"] == {U1: []}
    assert env.get(S + f"&uuids={U1}&range=2d").status_code == 400
    assert env.get(S + f"&uuids={U1}&kind=nope").status_code == 400


def test_series_scoreboard(env, world, run_snapshot):
    world.set_scoreboard("smp", ["kills"], [("Alice", "kills", 3), ("Ghost", "kills", 9)],
                         {"t": ["Alice", "Bob"]})
    run_snapshot(1_001_500)
    world.set_scoreboard("smp", ["kills"], [("Alice", "kills", 4)],
                         {"t": ["Alice", "Bob"]})
    run_snapshot(1_001_800)
    u = "/api/stats/series?kind=scoreboard&key=kills&scope=smp&from=1000000&to=1002000"
    j = env.get(u + f"&uuids={U1}").json()
    assert j["series"][U1] == [[1_000_000, 0], [1_001_500, 3], [1_001_800, 4]]


def _ins(world, rows, table="stat_totals"):
    c = sqlite3.connect(world.db_path)
    try:
        c.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?)", rows)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def totals_env(env, world):
    k = ("minecraft:mined", "minecraft:stone")
    _ins(world, [
        ("smp", *k, 1_000_000, 105, 5), ("smp", *k, 1_000_600, 109, 9),
        ("cmp", *k, 1_000_900, 2, 2), ("cmp", *k, 1_001_200, 4, 4),
        ("smp", *k, 1_001_200, 112, 12)])
    cache.clear()
    return env


def test_totals_server_vs_server(totals_env):
    j = totals_env.get(T + "&scopes=smp,cmps&from=1000000&to=1001500").json()
    assert j["series"]["smp"] == [[1_000_000, 5], [1_000_600, 9], [1_001_200, 12]]
    assert j["series"]["cmps"] == [[1_000_000, 0], [1_000_900, 2], [1_001_200, 4]]


def test_totals_multi_server_scope_sums_steps(totals_env):
    j = totals_env.get(T + "&scopes=all&from=1000700&to=1001500").json()
    assert j["series"]["all"] == [[1_000_700, 9], [1_000_900, 11], [1_001_200, 16]]


def test_totals_respects_include_bots(totals_env):
    a = totals_env.get(T + "&scopes=smp&from=1000000&to=1001500").json()
    b = totals_env.get(T + "&scopes=smp&include_bots=true&from=1000000&to=1001500").json()
    assert a["series"]["smp"][0][1] == 5 and b["series"]["smp"][0][1] == 105


def test_totals_empty_and_bad(totals_env):
    j = totals_env.get(T + "&scopes=smp,cmps&from=0&to=500").json()
    assert j["series"] == {"smp": [], "cmps": []}
    assert totals_env.get(T + "&scopes=nope").status_code == 400
    assert totals_env.get(T + "&scopes=").status_code == 400


def test_history_reads_seek_point_indexes(env, world):
    from api.routes import series as sr
    conn = sqlite3.connect(world.db_path)
    try:
        sc = resolve_scope(conn, "all")
        rng = Range(0, 10, 0, False)
        for kind, cat, key, idx in (("stat", "c", "k", "idx_stat_point"),
                                    ("scoreboard", "", "o", "idx_scoreboard_point")):
            for sql, params in zip(*[iter(sr._history_events(
                    conn, sc, False, kind, cat, key, ["a", "b"], rng))] * 2):
                plan = " | ".join(r[3] for r in conn.execute(
                    "EXPLAIN QUERY PLAN " + sql, params))
                assert f"USING INDEX {idx}" in plan or f"USING COVERING INDEX {idx}" in plan
    finally:
        conn.close()
