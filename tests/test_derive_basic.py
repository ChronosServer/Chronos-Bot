import sqlite3

import derive
import pytest

P = "aaaaaaaa-0000-0000-0000-000000000001"
BOT = "bbbbbbbb-0000-0000-0000-000000000002"


def pt(v):
    return {"minecraft:custom": {"minecraft:play_time": v}}


@pytest.fixture
def db(world):
    c = sqlite3.connect(world.db_path)
    yield c
    c.close()


def q(db, sql, *a):
    return db.execute(sql, a).fetchall()


def test_first_run_records_no_activity(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    assert q(db, "SELECT * FROM activity") == []
    assert q(db, "SELECT last_activity FROM players WHERE uuid=?", P) == [(None,)]


def test_playtime_change_records_activity(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    world.add_stats("smp", P, pt(20))
    run_snapshot(1900)
    assert q(db, "SELECT server_key, at, uuid FROM activity") == [("smp", 1900, P)]
    assert q(db, "SELECT last_activity FROM players WHERE uuid=?", P) == [(1900,)]


def test_totals_change_only(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    world.add_stats("smp", P, pt(20))
    run_snapshot(1900)
    n = q(db, "SELECT COUNT(*) FROM stat_totals")[0][0]
    assert n > 0
    assert q(db, "SELECT total FROM stat_totals_current WHERE server_key='smp'") == [(20,)]
    run_snapshot(2800)
    assert q(db, "SELECT COUNT(*) FROM stat_totals")[0][0] == n


def test_total_no_bots_excludes_smp_bot(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    world.add_stats("smp", BOT, pt(5))
    world.set_usercache("smp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    run_snapshot(1000)
    assert q(db, "SELECT total, total_no_bots FROM stat_totals_current "
                 "WHERE server_key='smp'") == [(15, 10)]


def test_scoreboard_totals(world, run_snapshot, db):
    world.set_usercache("smp", {P: "Alice"})
    world.set_scoreboard("smp", ["deaths"], [("Alice", "deaths", 3), ("Bob", "deaths", 4)],
                         {"t": ["Alice"]})
    run_snapshot(1000)
    rows = q(db, "SELECT objective, total FROM scoreboard_totals_current "
                 "WHERE server_key='smp'")
    assert rows == [("deaths", 3)]  # Bob is unresolved, excluded


def test_derive_failure_does_not_fail_snapshot(world, run_snapshot, db, monkeypatch):
    world.add_stats("smp", P, pt(10))

    def boom(conn, run_at):
        raise RuntimeError("boom")
    monkeypatch.setattr(derive, "derive_totals", boom)
    run_snapshot(1000)
    assert q(db, "SELECT COUNT(*) FROM stat_snapshots")[0][0] > 0
    assert q(db, "SELECT ok, error FROM snapshot_runs WHERE kind='derive'") == [(0, "boom")]


def test_meta_written(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    run_snapshot(1900)
    meta = dict(q(db, "SELECT key, value FROM meta"))
    last_real = q(db, "SELECT MAX(id) FROM snapshot_runs WHERE kind != 'derive'")[0][0]
    assert int(meta["data_version"]) in (last_real, q(db, "SELECT MAX(id) FROM snapshot_runs")[0][0])
    assert meta["tracking_started_at:smp"] == "1000"
    assert meta["last_run_at"] == "1900"
    assert q(db, "SELECT ok FROM snapshot_runs WHERE kind='derive'") == [(1,), (1,)]


def test_data_version_is_max_run_id_at_write_time(world, run_snapshot, db):
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    dv = int(q(db, "SELECT value FROM meta WHERE key='data_version'")[0][0])
    # derive's own run row is recorded right after write_meta
    assert dv == q(db, "SELECT MAX(id) FROM snapshot_runs")[0][0] - 1


def test_tracking_start_ignores_empty_runs(world, run_snapshot, db):
    run_snapshot(400)  # creates schema + servers rows
    db.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, rows_written) "
               "VALUES (500, 'smp', 'stats', 1, 0, 0)")
    db.commit()
    world.add_stats("smp", P, pt(10))
    run_snapshot(1000)
    meta = dict(q(db, "SELECT key, value FROM meta"))
    assert meta["tracking_started_at:smp"] == "1000"
    assert q(db, "SELECT * FROM activity") == []


def test_pseudo_all_row_not_provisioned(world, run_snapshot, db):
    run_snapshot(1000)
    assert q(db, "SELECT label, group_key, provisioned FROM servers WHERE key='all'") == [
        ("All", "all", 0)]
