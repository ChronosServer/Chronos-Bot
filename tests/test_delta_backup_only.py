"""Delta leaderboards were negative when a value existed only at an imported
backup point and was absent from the live files (0 - backup value)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import snapshot
from api import cache
from api.main import create_app
from conftest import make_backup
from tools import import_backups as ib

P = "5f2a15be-cdcc-49a6-a74a-1b31e0562786"   # DiggusMaximus123
Q = "22222222-2222-2222-2222-222222222222"
DAY = 86400
T1 = 1_700_000_000           # backup
L = T1 + 400 * DAY           # first live run
NOW = L + 3 * DAY
A = "/api/stats"


def other(n):
    return {"minecraft:custom": {"minecraft:jump": n}}


def import_backup(tmp_path, world, server, name, stats, scoreboard=None):
    conn = snapshot.connect_db(world.db_path)
    b = ib.read_backup(make_backup(tmp_path, name=name, last_played_ms=T1 * 1000, stats=stats,
                                   scoreboard=scoreboard, usercache={P: "Diggus", Q: "Q"}), None)
    ib.import_backups(conn, server, [b], {}, {}, False)
    conn.close()


@pytest.fixture
def env(tmp_path, world, run_snapshot, monkeypatch):
    """Live: P has no stone (key absent) and no Deaths entry; Q mines live.
    Backup (smp and cmp): P had stone 1108 / 50 and Deaths 3."""
    for key in ("smp", "cmp"):
        world.set_usercache(key, {P: "Diggus", Q: "Q"})
        world.add_stats(key, P, other(1))
        world.add_stats(key, Q, {"minecraft:mined": {"minecraft:stone": 10}})
    world.set_scoreboard("smp", ["Deaths"], [("Q", "Deaths", 1)], {"t": ["Diggus", "Q"]})
    run_snapshot(L)
    import_backup(tmp_path, world, "smp", "bs", {P: {"minecraft:mined": {"minecraft:stone": 1108}}},
                  (["Deaths"], [("Diggus", "Deaths", 3)]))
    import_backup(tmp_path, world, "cmp", "bc", {P: {"minecraft:mined": {"minecraft:stone": 50}}})
    world.add_stats("smp", Q, {"minecraft:mined": {"minecraft:stone": 15}})
    run_snapshot(L + DAY)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    monkeypatch.setattr("time.time", lambda: NOW)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world, c.run = world, run_snapshot
        yield c
    cache.clear()


def lb(c, qs):
    return {r["uuid"]: r["value"] for r in c.get(f"{A}/leaderboard?{qs}").json()["rows"]}


STONE = "kind=stat&category=minecraft:mined&key=minecraft:stone"


@pytest.mark.parametrize("scope", ["smp", "all", "cmps"])
def test_backup_only_value_gives_no_negative_delta(env, scope):
    rows = lb(env, f"{STONE}&scope={scope}&range=1w")
    assert all(v > 0 for v in rows.values()), rows
    assert P not in rows
    if scope != "cmps":
        assert rows == {Q: 5}


def test_scoreboard_backup_only_entry(env):
    assert lb(env, "kind=scoreboard&key=Deaths&scope=all&range=1w") == {}
    # history before live start still shows it; from live start on it is 0
    assert lb(env, f"kind=scoreboard&key=Deaths&scope=smp&to={L - 1}") == {P: 3}
    assert lb(env, "kind=scoreboard&key=Deaths&scope=smp") == {Q: 1}


def test_series_and_compare_agree(env):
    s = env.get(f"{A}/series?{STONE}&uuids={P}&scope=smp&range=1w").json()["series"][P]
    assert all(v == 0 for _, v in s), s       # was flat at 1108
    j = env.get(f"{A}/compare?uuids={P},{Q}&keys=minecraft:mined|minecraft:stone"
                f"&scope=all&range=1w").json()
    assert j["values"]["minecraft:mined|minecraft:stone"] == {P: 0, Q: 5}
    t = env.get(f"{A}/totals?{STONE}&scopes=smp&range=1w").json()["series"]["smp"]
    assert t[-1][1] - t[0][1] == 5


def test_range_across_live_start_clamps_stat_drop(env):
    # from before live start: P's stone went 1108 -> 0 (file lost): clamped, left out
    rows = lb(env, f"{STONE}&scope=smp&from={T1 - DAY}")
    assert rows == {Q: 15}
    j = env.get(f"{A}/compare?uuids={P},{Q}&keys=minecraft:mined|minecraft:stone"
                f"&scope=smp&from={T1 - DAY}").json()
    assert j["values"]["minecraft:mined|minecraft:stone"][P] == 0


def test_scoreboard_drop_keeps_sign(env):
    env.world.set_scoreboard("smp", ["Deaths"], [("Q", "Deaths", 0)], {"t": ["Diggus", "Q"]})
    env.run(L + 2 * DAY)
    cache.clear()
    assert lb(env, "kind=scoreboard&key=Deaths&scope=smp&range=1w") == {Q: -1}


def test_fill_leaves_keys_present_live_alone(tmp_path, world, run_snapshot):
    """Live DB case: a run before the counted first live run already wrote a
    full set of rows (it isn't counted, e.g. recorded with entries_read 0),
    and the counted run then wrote nothing for unchanged keys. Their values
    live in rows older than L; they are in the current tables and must not
    get a 0 at L."""
    import history_fill
    world.set_usercache("cmp", {P: "Diggus"})
    world.add_stats("cmp", P, {"minecraft:mined": {"minecraft:stone": 7}})
    world.set_scoreboard("cmp", ["digs"], [("Diggus", "digs", 4)], {})
    run_snapshot(L - DAY)
    run_snapshot(L)  # unchanged: writes nothing
    c = sqlite3.connect(world.db_path)
    c.execute("UPDATE snapshot_runs SET entries_read=0 WHERE at=?", (L - DAY,))
    assert history_fill.live_start(c, "cmp", "stats") == L
    assert history_fill.fill_server(c, "cmp") == (0, 0)
    # a key missing live does get its 0
    c.execute("INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
              "VALUES (?, 'cmp', ?, 'minecraft:mined', 'minecraft:dirt', 5)", (L - DAY, P))
    assert history_fill.fill_server(c, "cmp") == (1, 0)
    assert history_fill.fill_server(c, "cmp") == (0, 0)  # idempotent
    c.close()


def test_existing_db_filled_once_by_snapshot(env):
    w = env.world
    c = sqlite3.connect(w.db_path)
    zero = "SELECT COUNT(*) FROM stat_snapshots WHERE at=? AND value=0"
    assert c.execute(zero, (L,)).fetchone()[0] == 2  # smp + cmp, written by the import
    # an older DB: rows imported before this fix, never filled
    c.execute("DELETE FROM stat_snapshots WHERE at=? AND value=0", (L,))
    c.execute("DELETE FROM scoreboard_snapshots WHERE at=? AND value=0", (L,))
    c.execute("DELETE FROM meta WHERE key='live_start_filled'")
    c.commit()
    env.run(L + 2 * DAY)
    assert c.execute(zero, (L,)).fetchone()[0] == 2
    assert c.execute("SELECT value FROM meta WHERE key='live_start_filled'").fetchone() == ("1",)
    env.run(L + 3 * DAY)  # flag set: no second fill
    assert c.execute(zero, (L,)).fetchone()[0] == 2
    c.close()
