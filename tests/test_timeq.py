import sqlite3
from pathlib import Path

import pytest

from api import timeq
from api.scope import resolve_scope

SCHEMA = (Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text()
U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO servers(key,label,group_key,filters_bots) VALUES(?,?,?,?)",
                  [("smp", "SMP", "smp", 0), ("cmp", "CMP", "cmp", 0)])
    c.execute("INSERT INTO meta VALUES('tracking_started_at:smp','1000')")
    c.execute("INSERT INTO meta VALUES('tracking_started_at:cmp','500')")
    yield c
    c.close()


def add(c, server, uuid, at, value, cat="minecraft:mined", key="minecraft:stone"):
    c.execute("INSERT OR IGNORE INTO players VALUES(?,?,1,0,0,0)", (uuid, None))
    c.execute("INSERT INTO stat_snapshots(at,server_key,uuid,category,stat_key,value) "
              "VALUES(?,?,?,?,?,?)", (at, server, uuid, cat, key, value))


def test_value_at_uses_latest_row_before_t(conn):
    add(conn, "smp", U1, 100, 5)
    add(conn, "smp", U1, 200, 9)
    sc = resolve_scope(conn, "smp")
    f = lambda t: timeq.stat_values_at(conn, sc, True, "minecraft:mined", "minecraft:stone", t)
    assert f(150) == {U1: 5}
    assert f(250) == {U1: 9}
    assert f(50) == {}


def test_all_scope_sums_servers(conn):
    add(conn, "smp", U1, 100, 5)
    add(conn, "cmp", U1, 100, 7)
    sc = resolve_scope(conn, "all")
    assert timeq.stat_values_at(conn, sc, True, "minecraft:mined", "minecraft:stone", 300) == {U1: 12}


def test_range_clamped_to_tracking_start(conn):
    sc = resolve_scope(conn, "smp")
    r = timeq.parse_range(conn, sc, "1y", None, None, 5000)
    assert r.effective_from == 1000 and r.is_now and r.to_ts == 5000
    # all-scope uses the min over servers
    r = timeq.parse_range(conn, resolve_scope(conn, "all"), "all", None, None, 5000)
    assert r.effective_from == 500


def test_parse_range_errors(conn):
    sc = resolve_scope(conn, "smp")
    with pytest.raises(ValueError):
        timeq.parse_range(conn, sc, "2d", None, None, 5000)
    with pytest.raises(ValueError):
        timeq.parse_range(conn, sc, None, 4000, 3000, 5000)


def test_downsample_keeps_step_and_cap():
    pts = [(1000 + i, i) for i in range(5000)]
    out = timeq.downsample(pts, 1000, 6000, 500)
    assert len(out) <= 500
    assert out[0] == (1000, 0)
    assert out[-1][1] == pts[-1][1]
    assert [t for t, _ in out] == sorted(t for t, _ in out)
    small = timeq.downsample([(5, 3), (20, 4)], 10, 30)
    assert small == [(10, 3), (20, 4)]


def test_range_empty_when_to_before_tracking_start(conn):
    sc = resolve_scope(conn, "smp")
    r = timeq.parse_range(conn, sc, None, 0, 500, 5000)
    assert r.effective_from == 1000 and r.empty
    assert not timeq.parse_range(conn, sc, None, 0, 1000, 5000).empty


def _plan(conn, sql, params):
    return " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_history_queries_seek_point_indexes(conn):
    from api.scope import scoreboard_filter_sql, stat_filter_sql
    sc = resolve_scope(conn, "all")
    f, p = stat_filter_sql(sc, True, "s")
    plan = _plan(conn, timeq.stat_history_sql(f), ["c", "k", 1, *p])
    assert "idx_stat_point" in plan and "category=?" in plan and "stat_key=?" in plan
    f, p = scoreboard_filter_sql(sc, True, "s")
    plan = _plan(conn, timeq.scoreboard_history_sql(f), ["o", 1, *p])
    assert "idx_scoreboard_point" in plan and "objective=?" in plan


def test_downsample_repeated_ts_and_point_at_from():
    out = timeq.downsample([(10, 1), (10, 2), (20, 3), (20, 4)], 10, 30)
    assert out == [(10, 2), (20, 3), (20, 4)]
    big = [(10, 7)] + [(11 + i, i) for i in range(2000)] + [(500, -1)] * 3
    out = timeq.downsample(big, 10, 3000, 100)
    assert out[0] == (10, 7) and len(out) <= 100
