"""Backup import Task 3: rebuild derived data (firsts, milestones, totals,
badges) over the imported backup points."""
import hashlib
import sqlite3

import pytest

import derive_achievements as da
import snapshot
from api.settings import DEFAULTS
from conftest import make_backup
from derive_common import tracking_start
from tools import import_backups as ib

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
NAMES = {U1: "Alice", U2: "Bob"}
T1, T2 = 1_600_000_000, 1_630_000_000
LIVE = 1_800_000_000
STONE = ("minecraft:mined", "minecraft:stone")
DIRT = ("minecraft:mined", "minecraft:dirt")


def mined(**kv):
    return {"minecraft:mined": {f"minecraft:{k}": v for k, v in kv.items()}}


def live_run(world, run_snapshot, at, stats):
    """A live snapshot of smp; every player is a team member (counted)."""
    for u, st in stats.items():
        world.add_stats("smp", u, st)
    world.set_usercache("smp", NAMES)
    world.set_scoreboard("smp", [], [], {"team": list(NAMES.values())})
    run_snapshot(at)


def backup(tmp_path, name, at, stats):
    return ib.read_backup(make_backup(tmp_path, name=name, last_played_ms=at * 1000,
                                      stats=stats, usercache=NAMES), None)


def import_and_rebuild(world, tmp_path, specs, settings=DEFAULTS):
    conn = snapshot.connect_db(world.db_path)
    bs = [backup(tmp_path, name, at, st) for name, at, st in specs]
    ib.import_backups(conn, "smp", bs, {}, {}, False)
    counts = da.rebuild_from_points(conn, "smp", settings)
    return conn, counts


def milestones(conn, key=STONE):
    return conn.execute(
        "SELECT at, uuid, threshold, value_at, earliest_known, approximate FROM milestones "
        "WHERE server_key='smp' AND category=? AND stat_key=? ORDER BY threshold",
        key).fetchall()


def first(conn, key):
    return conn.execute(
        "SELECT at, uuid, earliest_known, approximate FROM firsts "
        "WHERE server_key='smp' AND category=? AND stat_key=?", key).fetchall()


def snapshot_of(conn):
    """Content of every derived table, ids excluded."""
    out = {}
    for t, cols in (("firsts", "at, server_key, uuid, category, stat_key, earliest_known, "
                               "approximate"),
                    ("milestones", "at, server_key, uuid, rule_key, category, stat_key, "
                                   "threshold, value_at, earliest_known, approximate"),
                    ("badges", "uuid, badge_key, server_key"),
                    ("stat_totals", "server_key, category, stat_key, at, total, total_no_bots"),
                    ("stat_totals_current", "*"), ("scoreboard_totals", "*"),
                    ("scoreboard_totals_current", "*")):
        h = hashlib.sha256()
        for r in sorted(conn.execute(f"SELECT {cols} FROM {t}"), key=repr):
            h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out


# ----------------------------------------------------------------- the rules

def test_tracking_start_moves_back(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=2000)})
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=500)})])
    assert tracking_start(conn, "smp") == T1
    assert conn.execute("SELECT value FROM meta WHERE key='tracking_started_at:smp'"
                        ).fetchone()[0] == str(T1)
    conn.close()


def test_milestone_between_backups_dated_at_later_point_approximate(tmp_path, world,
                                                                     run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=900)}),
                                                   ("b2", T2, {U1: mined(stone=1500)})])
    assert milestones(conn) == [(T2, U1, 1000, 1500, 0, 1)]
    conn.close()


def test_true_at_oldest_is_earliest_known(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=1500)})])
    assert milestones(conn) == [(T1, U1, 1000, 1500, 1, 0)]
    assert first(conn, STONE) == [(T1, U1, 1, 0)]
    conn.close()


def test_crossing_between_last_backup_and_first_live_run_dated_at_live_run(
        tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    conn = snapshot.connect_db(world.db_path)
    # the live seeding recorded it as "already had"
    assert milestones(conn) == [(LIVE, U1, 1000, 1500, 1, 0)]
    conn.close()
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=900)})])
    assert milestones(conn) == [(LIVE, U1, 1000, 1500, 0, 1)]
    conn.close()


def test_first_between_backups_approximate(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5, dirt=1), U2: mined(dirt=3)})
    conn, _ = import_and_rebuild(world, tmp_path, [
        ("b1", T1, {U1: mined(stone=5)}),
        ("b2", T2, {U1: mined(stone=5, dirt=1), U2: mined(dirt=3)})])
    assert first(conn, STONE) == [(T1, U1, 1, 0)]
    assert first(conn, DIRT) == [(T2, U2, 0, 1)]  # top holder at the later point
    conn.close()


def test_live_real_rows_untouched(tmp_path, world, run_snapshot):
    """Review Focus 5: a live milestone/first for the same threshold/key that
    the backups would place earlier is kept, and no duplicate is created."""
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=900)})
    live_run(world, run_snapshot, LIVE + 1000, {U1: mined(stone=1500),
                                                U2: mined(diamond_ore=1)})
    conn = snapshot.connect_db(world.db_path)
    real = ("SELECT * FROM {} WHERE earliest_known=0 AND approximate=0 ORDER BY id")
    before = (conn.execute(real.format("milestones")).fetchall(),
              conn.execute(real.format("firsts")).fetchall())
    assert len(before[0]) == 1 and len(before[1]) == 1
    conn.close()
    conn, _ = import_and_rebuild(world, tmp_path, [
        ("b1", T1, {U1: mined(stone=100)}),
        ("b2", T2, {U1: mined(stone=1200, diamond_ore=2)})])
    assert (conn.execute(real.format("milestones")).fetchall(),
            conn.execute(real.format("firsts")).fetchall()) == before
    assert milestones(conn) == [(LIVE + 1000, U1, 1000, 1500, 0, 0)]
    assert first(conn, ("minecraft:mined", "minecraft:diamond_ore")) == [
        (LIVE + 1000, U2, 0, 0)]
    conn.close()


def test_totals_history_from_oldest_backup_no_cliff(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=900)})
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=500)}),
                                                   ("b2", T2, {U1: mined(stone=700)})])
    assert conn.execute(
        "SELECT at, total, total_no_bots FROM stat_totals WHERE server_key='smp' "
        "AND category=? AND stat_key=? ORDER BY at", STONE).fetchall() == [
        (T1, 500, 500), (T2, 700, 700), (LIVE, 900, 900)]
    assert conn.execute(
        "SELECT total, total_no_bots FROM stat_totals_current WHERE server_key='smp' "
        "AND category=? AND stat_key=?", STONE).fetchone() == (900, 900)
    assert conn.execute("SELECT value FROM meta WHERE key='totals_backfilled'"
                        ).fetchone()[0] == "1"
    conn.close()


def test_approximate_first_counts_for_firsts_badge(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5), U2: mined(dirt=3)})
    conn, counts = import_and_rebuild(world, tmp_path, [
        ("b1", T1, {U1: mined(stone=5)}),
        ("b2", T2, {U1: mined(stone=5), U2: mined(dirt=3)})])
    got = {(u, b) for u, b in conn.execute(
        "SELECT uuid, badge_key FROM badges WHERE badge_key LIKE 'firsts:%'")}
    assert got == {(U2, "firsts:1")}  # U1 holds only an earliest_known first
    assert counts["badges"] >= 1
    conn.close()


def test_seed_marker_stays_set(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5)})
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=5)})])
    assert conn.execute("SELECT value FROM meta WHERE key='seeded_achievements'"
                        ).fetchone()[0] == "1"
    conn.close()


def test_rows_between_live_start_and_seeding_keep_earliest_known(tmp_path, world,
                                                                  run_snapshot):
    """Live history between the first live run and the first derive (seeding)
    isn't a point: what was seeded from current values stays earliest_known."""
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=900)})
    conn = snapshot.connect_db(world.db_path)
    # Emulate seeding that happened later than the first live run: a live run
    # at LIVE+500 with no derive, then seeding at LIVE+1000.
    for t in ("firsts", "milestones"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM meta WHERE key='seeded_achievements'")
    conn.execute("INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
                 "VALUES (?, 'smp', ?, ?, ?, 1500)", (LIVE + 500, U1, *STONE))
    conn.execute("UPDATE stat_current SET value=1500 WHERE uuid=? AND stat_key=?",
                 (U1, STONE[1]))
    conn.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, "
                 "rows_written) VALUES (?, 'smp', 'stats', 1, 1, 1)", (LIVE + 500,))
    conn.commit()
    da.derive_firsts(conn, LIVE + 1000)
    da.derive_milestones(conn, LIVE + 1000, DEFAULTS)
    assert milestones(conn) == [(LIVE + 1000, U1, 1000, 1500, 1, 0)]
    conn.close()
    conn, _ = import_and_rebuild(world, tmp_path, [("b1", T1, {U1: mined(stone=100)})])
    # crossed after the last point (the first live run): still "already had",
    # and keeps its original seeding date across rebuilds
    assert milestones(conn) == [(LIVE + 1000, U1, 1000, 1500, 1, 0)]
    assert first(conn, STONE) == [(T1, U1, 1, 0)]  # held at the oldest point
    da.rebuild_from_points(conn, "smp", DEFAULTS)
    assert milestones(conn) == [(LIVE + 1000, U1, 1000, 1500, 1, 0)]
    assert first(conn, STONE) == [(T1, U1, 1, 0)]  # held at the oldest point
    conn.close()


def test_scoreboard_objectives_rebuilt(tmp_path, world, run_snapshot):
    world.set_usercache("smp", NAMES)
    world.add_stats("smp", U1, mined(stone=1))
    world.set_scoreboard("smp", ["digs"], [("Alice", "digs", 5)], {"team": ["Alice"]})
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    b1 = ib.read_backup(make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000,
                                    stats={U1: mined(stone=1)}, usercache=NAMES,
                                    scoreboard=(["digs"], [])), None)
    b2 = ib.read_backup(make_backup(tmp_path, name="b2", last_played_ms=T2 * 1000,
                                    stats={U1: mined(stone=1)}, usercache=NAMES,
                                    scoreboard=(["digs"], [("Alice", "digs", 2)])), None)
    ib.import_backups(conn, "smp", [b1, b2], {}, {}, False)
    da.rebuild_from_points(conn, "smp", DEFAULTS)
    assert first(conn, ("scoreboard", "digs")) == [(T2, U1, 0, 1)]
    conn.close()


# ----------------------------------------------------------------- rerun / safety

def test_full_rerun_is_noop(tmp_path, world, run_snapshot):
    """Review Focus 3: rerunning the import adds nothing and the rebuild is
    a no-op (same derived content, same counts)."""
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500, dirt=2), U2: mined(dirt=3)})
    w1 = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000,
                     stats={U1: mined(stone=900)}, usercache=NAMES)
    w2 = make_backup(tmp_path, name="b2", last_played_ms=T2 * 1000,
                     stats={U1: mined(stone=1200, dirt=1), U2: mined(dirt=3)},
                     usercache=NAMES)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")
    args = ["--server", "smp", "--no-backup", "--config", str(cfg), str(w1), str(w2)]
    assert ib.main(args, dictionary={}) == 0
    conn = snapshot.connect_db(world.db_path)
    tables = ("stat_snapshots", "scoreboard_snapshots", "snapshot_runs", "imports",
              "firsts", "milestones", "badges", "stat_totals")
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    derived = snapshot_of(conn)
    conn.close()

    assert ib.main(args, dictionary={}) == 0
    conn = snapshot.connect_db(world.db_path)
    assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables} == counts
    assert snapshot_of(conn) == derived
    # and calling the rebuild itself again changes nothing either
    c1 = da.rebuild_from_points(conn, "smp", DEFAULTS)
    c2 = da.rebuild_from_points(conn, "smp", DEFAULTS)
    assert c1 == c2
    assert snapshot_of(conn) == derived
    conn.close()


def test_rebuild_busy_aborts(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5)})
    conn = snapshot.connect_db(world.db_path)
    other = sqlite3.connect(world.db_path)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(da.DatabaseBusy, match="a snapshot is running"):
            da.rebuild_from_points(conn, "smp", DEFAULTS, lock_timeout_s=0.1)
    finally:
        other.rollback()
        other.close()
    assert not conn.in_transaction
    conn.close()


def test_rebuild_is_all_or_nothing(tmp_path, world, run_snapshot, monkeypatch):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    conn = snapshot.connect_db(world.db_path)
    ib.import_backups(conn, "smp", [backup(tmp_path, "b1", T1, {U1: mined(stone=900)})],
                      {}, {}, False)
    before = snapshot_of(conn)

    def boom(*a, **k):
        raise RuntimeError("badges failed")
    monkeypatch.setattr(da, "_derive_badges", boom)
    with pytest.raises(RuntimeError, match="badges failed"):
        da.rebuild_from_points(conn, "smp", DEFAULTS)
    assert not conn.in_transaction
    assert snapshot_of(conn) == before
    conn.close()


def test_point_sources_are_pinned(tmp_path, world, run_snapshot):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5)})
    conn = snapshot.connect_db(world.db_path)
    plans = []
    for src, params, _ in da._point_sources("smp", LIVE):
        plans.append(" ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM (" + src + ")", params)))
    assert "idx_stat_point" in plans[0]
    assert "idx_scoreboard_point" in plans[1]
    conn.close()


# ----------------------------------------------------------------- CLI

def test_main_end_to_end_cli(tmp_path, world, run_snapshot, capsys):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    w1 = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000,
                     stats={U1: mined(stone=900)}, usercache=NAMES)
    w2 = make_backup(tmp_path, name="b2", last_played_ms=T2 * 1000,
                     stats={U1: mined(stone=1200)}, usercache=NAMES)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")
    rc = ib.main(["--server", "smp", "--no-backup", "--config", str(cfg), str(w1), str(w2)],
                 dictionary={})
    out = capsys.readouterr().out
    assert rc == 0
    assert "total: 2 imported" in out
    assert "rebuild:" in out and "milestones" in out
    conn = snapshot.connect_db(world.db_path)
    assert milestones(conn) == [(T2, U1, 1000, 1200, 0, 1)]
    conn.close()


def test_main_rebuild_busy_exits_1(tmp_path, world, run_snapshot, capsys, monkeypatch):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5)})
    w1 = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000,
                     stats={U1: mined(stone=1)}, usercache=NAMES)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")

    def busy(conn, key, settings, **kw):
        raise da.DatabaseBusy(da.BUSY_MESSAGE)
    monkeypatch.setattr(da, "rebuild_from_points", busy)
    rc = ib.main(["--server", "smp", "--no-backup", "--config", str(cfg), str(w1)],
                 dictionary={})
    assert rc == 1
    assert "a snapshot is running" in capsys.readouterr().out


def test_main_rebuild_only(tmp_path, world, run_snapshot, capsys):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=1500)})
    conn = snapshot.connect_db(world.db_path)
    ib.import_backups(conn, "smp", [backup(tmp_path, "b1", T1, {U1: mined(stone=900)})],
                      {}, {}, False)
    assert milestones(conn) == [(LIVE, U1, 1000, 1500, 1, 0)]  # not rebuilt yet
    conn.close()
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")
    assert ib.main(["--server", "smp", "--rebuild-only", "--config", str(cfg)]) == 0
    assert "rebuild:" in capsys.readouterr().out
    conn = snapshot.connect_db(world.db_path)
    assert milestones(conn) == [(LIVE, U1, 1000, 1500, 0, 1)]
    conn.close()


def test_main_without_worlds_exits_2(tmp_path, world, capsys):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")
    assert ib.main(["--server", "smp", "--config", str(cfg)]) == 2
    assert "world folder" in capsys.readouterr().out


# ----------------------------------------------------------------- fix round 1

def recompute_matches(conn):
    """*_totals_current agrees with the live current-based recompute."""
    stat = dict(((c, k), t) for c, k, t in conn.execute(
        "SELECT category, stat_key, SUM(value) FROM stat_current WHERE server_key='smp' "
        "GROUP BY category, stat_key"))
    for ck, t in stat.items():
        assert conn.execute("SELECT total FROM stat_totals_current WHERE server_key='smp' "
                            "AND category=? AND stat_key=?", ck).fetchone() == (t,)


def test_backup_only_player_drops_out_at_first_live_run(tmp_path, world, run_snapshot):
    """Reviewer repro: U2 is only in a backup. Totals drop at the first live
    run (a full observation), and the next unchanged live run writes nothing."""
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=2000)})
    conn, _ = import_and_rebuild(world, tmp_path, [
        ("b1", T1, {U1: mined(stone=500), U2: mined(stone=300)})])
    assert conn.execute(
        "SELECT at, total FROM stat_totals WHERE server_key='smp' AND category=? "
        "AND stat_key=? ORDER BY at", STONE).fetchall() == [(T1, 800), (LIVE, 2000)]
    recompute_matches(conn)
    before = conn.execute("SELECT COUNT(*) FROM stat_totals").fetchone()[0]
    conn.close()
    live_run(world, run_snapshot, LIVE + 3600, {U1: mined(stone=2000)})
    conn = snapshot.connect_db(world.db_path)
    assert conn.execute("SELECT COUNT(*) FROM stat_totals").fetchone()[0] == before
    # per-player history is untouched: U2 keeps its backup row, no fake 0
    assert conn.execute("SELECT at, value FROM stat_snapshots WHERE uuid=?",
                        (U2,)).fetchall() == [(T1, 300)]
    conn.close()


def test_scoreboard_reset_and_backup_only_objective_drop_at_first_live_run(
        tmp_path, world, run_snapshot):
    world.set_usercache("smp", NAMES)
    world.add_stats("smp", U1, mined(stone=1))
    team = {"team": list(NAMES.values())}
    world.set_scoreboard("smp", ["digs"], [("Alice", "digs", 5)], team)  # Bob reset
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    b1 = ib.read_backup(make_backup(
        tmp_path, name="b1", last_played_ms=T1 * 1000,
        stats={U1: mined(stone=1), U2: mined(stone=1)}, usercache=NAMES,
        scoreboard=(["digs", "oldobj"], [("Alice", "digs", 4), ("Bob", "digs", 7),
                                         ("Alice", "oldobj", 3)])), None)
    ib.import_backups(conn, "smp", [b1], {}, {}, False)
    da.rebuild_from_points(conn, "smp", DEFAULTS)

    def series(obj):
        return conn.execute("SELECT at, total FROM scoreboard_totals WHERE server_key='smp' "
                            "AND objective=? ORDER BY at", (obj,)).fetchall()
    assert series("digs") == [(T1, 11), (LIVE, 5)]
    assert series("oldobj") == [(T1, 3), (LIVE, 0)]
    assert conn.execute("SELECT objective, total FROM scoreboard_totals_current "
                        "WHERE server_key='smp' ORDER BY objective").fetchall() == [
        ("digs", 5), ("oldobj", 0)]
    before = (conn.execute("SELECT COUNT(*) FROM scoreboard_totals").fetchone()[0],
              conn.execute("SELECT COUNT(*) FROM stat_totals").fetchone()[0])
    conn.close()
    run_snapshot(LIVE + 3600)  # nothing changed in game
    conn = snapshot.connect_db(world.db_path)
    assert (conn.execute("SELECT COUNT(*) FROM scoreboard_totals").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM stat_totals").fetchone()[0]) == before
    conn.close()


def test_main_rebuild_only_rejects_worlds_and_dry_run(tmp_path, world, run_snapshot, capsys):
    live_run(world, run_snapshot, LIVE, {U1: mined(stone=5)})
    cfg = tmp_path / "cfg.json"
    cfg.write_text(__import__("json").dumps(world.config), encoding="utf-8")
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(stone=1)})
    for extra in (["--dry-run"], [str(w)]):
        assert ib.main(["--server", "smp", "--rebuild-only", "--config", str(cfg), *extra]) == 2
        assert "--rebuild-only" in capsys.readouterr().out
