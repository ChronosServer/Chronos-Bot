"""Ranges starting in the sparse gap before live tracking (I2).

Before a server's first live run L, history exists only at import points.
A range starting at from_ts < L is measured from the next real observation
(the smallest import point >= from_ts, or L), never from a stale backup."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import snapshot
from api import cache, history, timeq
from api.main import create_app
from api.scope import resolve_scope
from conftest import make_backup
from derive_achievements import rebuild_from_points
from api.settings import DEFAULTS
from derive_common import clamped_ago
from tools import import_backups as ib

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
NAMES = {U1: "Alice", U2: "Bob"}
DAY = 86400
T1, T2 = 1_600_000_000, 1_630_000_000
L = 1_800_000_000
L2 = L + 2 * DAY
STONE = ("minecraft:mined", "minecraft:stone")


def st(stone, play):
    return {"minecraft:mined": {"minecraft:stone": stone},
            "minecraft:custom": {"minecraft:play_time": play}}


def live_run(world, run_snapshot, at, stats):
    for u, s in stats.items():
        world.add_stats("smp", u, s)
    world.set_usercache("smp", NAMES)
    world.set_scoreboard("smp", [], [], {"team": list(NAMES.values())})
    world.add_stats("cmp", U1, st(5, 1))  # cmp: live only, no imports
    run_snapshot(at)


@pytest.fixture
def imported(tmp_path, world, run_snapshot):
    """smp: backups at T1 < T2, first live run L, second live run L2."""
    # A (U1) leads B (U2) on stone; at T2 the gap was 10000, at L 1000, now 150.
    live_run(world, run_snapshot, L, {U1: st(100_000, 50), U2: st(99_000, 50)})
    conn = snapshot.connect_db(world.db_path)
    bs = [ib.read_backup(make_backup(tmp_path, name=n, last_played_ms=at * 1000,
                                     stats=s, usercache=NAMES), None)
          for n, at, s in (("b1", T1, {U1: st(10_000, 10), U2: st(1_000, 10)}),
                           ("b2", T2, {U1: st(50_000, 20), U2: st(40_000, 20)}))]
    ib.import_backups(conn, "smp", bs, {}, {}, False)
    rebuild_from_points(conn, "smp", DEFAULTS)
    conn.close()
    live_run(world, run_snapshot, L2, {U1: st(100_100, 60), U2: st(99_950, 60)})
    conn = sqlite3.connect(world.db_path)
    yield conn
    conn.close()


def rng(conn, scope, **kw):
    return timeq.parse_range(conn, resolve_scope(conn, scope, {"cmps": "cmp"}),
                             kw.get("range"), kw.get("from_"), kw.get("to"),
                             kw.get("now", L2 + DAY), presets={"1w": 7 * DAY, "1y": 365 * DAY})


# ---------------------------------------------------------------- pure rule

def test_snap_from_rule():
    pts = [10, 20, 30]  # imports 10, 20; live start 30
    assert history.snap_from(5, pts, 30) == 5      # before tracking: the clamp's job
    assert history.snap_from(10, pts, 30) == 10
    assert history.snap_from(11, pts, 30) == 20
    assert history.snap_from(21, pts, 30) == 30
    assert history.snap_from(30, pts, 30) == 30
    assert history.snap_from(31, pts, 30) == 31    # dense live history
    assert history.snap_from(31, [], None) == 31   # no points known: unchanged
    assert history.snap_from(15, [10, 20], None) == 20  # imports only, no live run yet
    assert history.snap_from(25, [10, 20], None) == 25


def test_meta_points_written(imported):
    m = dict(imported.execute("SELECT key, value FROM meta").fetchall())
    assert json.loads(m["history_points:smp"]) == [T1, T2, L]
    assert m["live_started_at:smp"] == str(L)
    assert json.loads(m["history_points:cmp"]) == [L]
    assert history.meta_points(imported, "smp") == ([T1, T2, L], L)
    assert history.meta_points(imported, "nope") == ([], None)


# ---------------------------------------------------------------- API ranges

def test_week_range_in_gap_snaps_to_live_start(imported):
    now = L + 3 * DAY  # now - 1w = L - 4 days: between T2 and L
    r = rng(imported, "smp", range="1w", now=now)
    assert r.from_ts == now - 7 * DAY and r.effective_from == L


def test_from_between_backups_snaps_to_next_backup(imported):
    assert rng(imported, "smp", from_=T1 + DAY).effective_from == T2
    assert rng(imported, "smp", from_=T1).effective_from == T1


def test_from_after_live_start_unchanged(imported):
    assert rng(imported, "smp", from_=L + 10).effective_from == L + 10


def test_range_all_and_before_tracking_unchanged(imported):
    assert rng(imported, "smp", range="all").effective_from == T1
    assert rng(imported, "smp", from_=T1 - 100 * DAY).effective_from == T1
    # the all scope: tracking start is the min over servers (cmp starts at L)
    assert rng(imported, "all", range="all").effective_from == T1


def test_multi_server_scope_takes_max(imported):
    # smp snaps to L; cmp (no imports, live since L) is unchanged
    assert rng(imported, "all", from_=L - 4 * DAY).effective_from == L
    assert rng(imported, "all", from_=T1 + DAY).effective_from == T2


def test_server_without_imports_as_before(imported):
    assert rng(imported, "cmp", from_=L - 4 * DAY).effective_from == L  # clamp to start
    assert rng(imported, "cmp", from_=L + 5).effective_from == L + 5


def test_leaderboard_delta_from_live_start_not_lifetime(imported, world, monkeypatch):
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        j = c.get("/api/stats/leaderboard?kind=stat&category=minecraft:mined"
                  f"&key=minecraft:stone&scope=smp&from={L - 4 * DAY}&to={L2 + DAY}").json()
    cache.clear()
    assert j["effective_from"] == L
    # from L: +100 / +950. From T2 it would be 50100 / 59950; lifetime 100100 / 99950.
    assert {r["uuid"]: r["value"] for r in j["rows"]} == {U1: 100, U2: 950}


# ---------------------------------------------------------------- derive baselines

def test_clamped_ago_snaps(imported):
    assert clamped_ago(imported, "smp", L2, 7 * DAY) == L
    assert clamped_ago(imported, "smp", L2, 1 * DAY) == L2 - DAY
    assert clamped_ago(imported, "smp", T2 + DAY, 0) == L          # in the gap: next point
    assert clamped_ago(imported, "smp", T2 + DAY, 2 * DAY) == T2  # before T2: snap to T2
    assert clamped_ago(imported, "cmp", L2, 7 * DAY) == L         # no imports: as before


def test_rivalry_gap_7d_ago_uses_snapped_baseline(imported, world, monkeypatch):
    rows = imported.execute("SELECT stat_key, uuid_a, uuid_b, gap, gap_7d_ago FROM rivalries "
                            "WHERE server_key='smp'").fetchall()
    # gap at L was 1000 (at T2 it was 10000)
    assert rows == [(STONE[1], U1, U2, 150, 1000)]
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        [r] = c.get("/api/stats/rivalries?scope=smp").json()["rows"]
    cache.clear()
    # the window is L..L2 (2 days), not 7 days
    assert r["closing_per_day"] == pytest.approx((1000 - 150) / 2)


def test_spotlight_mover_uses_snapped_baseline(imported):
    rows = imported.execute(
        "SELECT uuid, stat_key, value, detail_json FROM spotlight_candidates "
        "WHERE trigger='mover' AND server_key='smp' AND stat_key=?", (STONE[1],)).fetchall()
    # from L: U1 +100 (< min_delta), U2 +950 (< min_delta): no mover. From T2
    # both would be movers (+50100, +59950).
    assert rows == []


def test_spotlight_mover_from_live_start(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, L, {U1: st(2_000, 50), U2: st(10_000, 50)})
    conn = snapshot.connect_db(world.db_path)
    b = ib.read_backup(make_backup(tmp_path, name="b2", last_played_ms=T2 * 1000,
                                   stats={U1: st(100, 20), U2: st(1_000, 20)},
                                   usercache=NAMES), None)
    ib.import_backups(conn, "smp", [b], {}, {}, False)
    rebuild_from_points(conn, "smp", DEFAULTS)
    conn.close()
    live_run(world, run_snapshot, L2, {U1: st(4_000, 60), U2: st(12_000, 60)})
    conn = sqlite3.connect(world.db_path)
    rows = conn.execute(
        "SELECT uuid, detail_json FROM spotlight_candidates WHERE trigger='mover' "
        "AND server_key='smp' AND stat_key=?", (STONE[1],)).fetchall()
    conn.close()
    # U1: 2000 -> 4000 from L (+2000, +100%): a mover, measured from L, not T2.
    # U2: +2000 is < 50% of 10000: not a mover (from T2 it would be +11000).
    assert [(u, json.loads(d)) for u, d in rows] == [(U1, {"from": 2000, "delta": 2000})]
