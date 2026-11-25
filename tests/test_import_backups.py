"""tools/import_backups.py: reading old world backups into history (Task 2)."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import snapshot
from conftest import make_backup
from tools import import_backups as ib

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
T1, T2, T3 = 1_600_000_000, 1_630_000_000, 1_660_000_000  # backup dates (s)
LIVE = 1_800_000_000
RENAMES = {"minecraft:custom": {"minecraft:play_one_minute": "minecraft:play_time"},
           "*": {"minecraft:grass_path": "minecraft:dirt_path",
                 "minecraft:grass": "minecraft:short_grass"}}


def mined(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


@pytest.fixture
def db(world):
    conn = snapshot.connect_db(world.db_path)
    snapshot.sync_servers_table(conn, world.config)
    yield conn
    conn.close()


def backup(tmp_path, name, at, stats, **kw):
    return ib.read_backup(make_backup(tmp_path, name=name, last_played_ms=at * 1000,
                                      stats=stats, **kw), None)


def rows(conn, sql="SELECT at, uuid, category, stat_key, value FROM stat_snapshots "
                   "ORDER BY at, uuid, category, stat_key"):
    return conn.execute(sql).fetchall()


def run(conn, backups, dry_run=False, dictionary=None, **kw):
    return ib.import_backups(conn, "smp", backups, RENAMES,
                             {} if dictionary is None else dictionary, dry_run, **kw)


# ------------------------------------------------------------------ reading

def test_read_backup_old_layout(tmp_path):
    w = make_backup(tmp_path, layout="old", last_played_ms=T1 * 1000 + 999,
                    stats={U1: mined(1)}, scoreboard=(["digs"], [("a", "digs", 1)]),
                    usercache={U1: "Alice"}, level_name="SMP", version_name="1.16.5",
                    data_version=2586)
    b = ib.read_backup(w, None)
    assert b.at == T1
    assert b.stats_dir == w / "stats"
    assert b.scoreboard_path == w / "data" / "scoreboard.dat"
    assert (b.level_name, b.version_name, b.data_version) == ("SMP", "1.16.5", 2586)
    assert b.usercache == {U1: "Alice"}
    assert b.layout == "old"


def test_read_backup_new_layout(tmp_path):
    w = make_backup(tmp_path, layout="new", last_played_ms=T1 * 1000,
                    stats={U1: mined(1)}, scoreboard=(["digs"], []))
    b = ib.read_backup(w, None)
    assert b.stats_dir == w / "players" / "stats"
    assert b.scoreboard_path == w / "data" / "minecraft" / "scoreboard.dat"
    assert b.layout == "new"


def test_date_override(tmp_path):
    w = make_backup(tmp_path, last_played_ms=T1 * 1000, stats={U1: mined(1)})
    assert ib.read_backup(w, T2).at == T2


def test_missing_date_refused(tmp_path):
    w = make_backup(tmp_path, last_played_ms=None, stats={U1: mined(1)})
    with pytest.raises(ValueError, match="date"):
        ib.read_backup(w, None)
    assert ib.read_backup(w, T1).at == T1


def _flat(world, u):
    (world / "stats" / f"{u}.json").write_text(
        json.dumps({"stat.mineBlock.minecraft.stone": 5}), encoding="utf-8")


def test_pre113_format_refused(tmp_path):
    """Only flat (pre-1.13) files: nothing importable, refused."""
    w = make_backup(tmp_path, last_played_ms=T1 * 1000, stats={})
    _flat(w, U1)
    with pytest.raises(ValueError, match="pre-1.13 stats format is not supported"):
        ib.read_backup(w, None)


def test_mixed_flat_and_modern_imports_modern(tmp_path, db, capsys):
    """SMP began on 1.12: players who never came back after 1.13 keep flat
    files in every backup. Those are skipped one by one and counted."""
    w = make_backup(tmp_path, last_played_ms=T1 * 1000, stats={U1: mined(5)})
    _flat(w, U2)
    _flat(w, U3)
    b = ib.read_backup(w, None)
    assert b.flat_files == 2
    [r] = run(db, [b])
    assert r.players == 1
    assert rows(db) == [(T1, U1, "minecraft:mined", "minecraft:stone", 5)]
    assert db.execute("SELECT uuid FROM players").fetchall() == [(U1,)]
    ib._print_result(r, dry_run=True)
    assert "pre-1.13 stat files skipped: 2" in capsys.readouterr().out


def test_main_reports_flat_files_skipped(tmp_path, world, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    _flat(w, U2)
    cfg = str(write_config(tmp_path, world))
    assert ib.main(["--server", "smp", "--dry-run", "--config", cfg, str(w)],
                   dictionary={}) == 0
    assert "pre-1.13 stat files skipped: 1" in capsys.readouterr().out
    assert ib.main(["--server", "smp", "--no-backup", "--config", cfg, str(w)],
                   dictionary={}) == 0
    assert "pre-1.13 stat files skipped: 1" in capsys.readouterr().out
    assert db.execute("SELECT COUNT(*) FROM stat_snapshots").fetchone()[0] == 1


def test_existing_player_first_seen_moves_back(tmp_path, world, run_snapshot):
    """A live player found in an older backup was there earlier: first_seen
    moves back to the backup date (never forward)."""
    world.add_stats("smp", U1, mined(9))
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    run(conn, [backup(tmp_path, "b2", T2, {U1: mined(5)})])
    assert conn.execute("SELECT first_seen, last_observed FROM players WHERE uuid=?",
                        (U1,)).fetchone() == (T2, LIVE)
    run(conn, [backup(tmp_path, "b1", T1, {U1: mined(1)})])
    assert conn.execute("SELECT first_seen FROM players WHERE uuid=?",
                        (U1,)).fetchone()[0] == T1
    conn.close()


def test_parse_date_arg():
    assert ib.parse_date("2021-06-01") == 1622505600


# ------------------------------------------------------------------ importing

def test_backup_after_live_start_refused(tmp_path, world, run_snapshot):
    world.add_stats("smp", U1, mined(5))
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    late = backup(tmp_path, "late", LIVE + 10, {U1: mined(1)})
    same = backup(tmp_path, "same", LIVE, {U1: mined(1)})
    ok = backup(tmp_path, "ok", T1, {U1: mined(1)})
    res = {r.path.parent.name: r for r in run(conn, [late, same, ok])}
    assert "on or after" in res["late"].skipped_reason
    assert "on or after" in res["same"].skipped_reason
    assert res["ok"].skipped_reason is None
    assert conn.execute("SELECT COUNT(*) FROM stat_snapshots WHERE at > ?",
                        (T1,)).fetchone()[0] == 1  # only the live row
    conn.close()


def test_renames_merge_into_current_key(tmp_path, db):
    b = backup(tmp_path, "b", T1, {U1: {"minecraft:custom": {"minecraft:play_one_minute": 100},
                                        "minecraft:mined": {"minecraft:grass_path": 3}}})
    [r] = run(db, [b])
    assert rows(db) == [(T1, U1, "minecraft:custom", "minecraft:play_time", 100),
                        (T1, U1, "minecraft:mined", "minecraft:dirt_path", 3)]
    assert r.renamed_hits == {"minecraft:custom/minecraft:play_one_minute": 1,
                              "minecraft:mined/minecraft:grass_path": 1}


def test_old_and_new_key_same_file_summed(tmp_path, db):
    b = backup(tmp_path, "b", T1, {U1: {"minecraft:custom": {
        "minecraft:play_one_minute": 100, "minecraft:play_time": 40}}})
    run(db, [b])
    assert rows(db) == [(T1, U1, "minecraft:custom", "minecraft:play_time", 140)]


def test_change_only_across_backups(tmp_path, db):
    b1 = backup(tmp_path, "b1", T1, {U1: {"minecraft:mined": {"minecraft:stone": 5,
                                                              "minecraft:dirt": 1}}})
    b2 = backup(tmp_path, "b2", T2, {U1: {"minecraft:mined": {"minecraft:stone": 5,
                                                              "minecraft:dirt": 2}}})
    b3 = backup(tmp_path, "b3", T3, {U1: {"minecraft:mined": {"minecraft:stone": 9,
                                                              "minecraft:dirt": 2}}})
    res = run(db, [b3, b1, b2])  # argument order doesn't matter: oldest first
    assert [r.at for r in res] == [T1, T2, T3]
    assert [r.rows_stats for r in res] == [2, 1, 1]
    assert rows(db) == [(T1, U1, "minecraft:mined", "minecraft:dirt", 1),
                        (T1, U1, "minecraft:mined", "minecraft:stone", 5),
                        (T2, U1, "minecraft:mined", "minecraft:dirt", 2),
                        (T3, U1, "minecraft:mined", "minecraft:stone", 9)]
    runs = db.execute("SELECT at, kind, ok, entries_read, rows_written FROM snapshot_runs "
                      "WHERE kind='import' ORDER BY at").fetchall()
    assert runs == [(T1, "import", 1, 1, 2), (T2, "import", 1, 1, 1),
                    (T3, "import", 1, 1, 1)]
    imp = db.execute("SELECT server_key, last_played, level_name, data_version, rows_written "
                     "FROM imports ORDER BY last_played").fetchall()
    assert imp == [("smp", T1, "world", 2586, 2), ("smp", T2, "world", 2586, 1),
                   ("smp", T3, "world", 2586, 1)]


def test_change_only_against_earlier_import_run(tmp_path, db):
    run(db, [backup(tmp_path, "b1", T1, {U1: mined(5)})])
    [r] = run(db, [backup(tmp_path, "b2", T2, {U1: mined(5), U2: mined(1)})])
    assert r.rows_stats == 1
    assert rows(db)[-1] == (T2, U2, "minecraft:mined", "minecraft:stone", 1)


def test_same_date_later_wins_warns(tmp_path, db, capsys):
    a = backup(tmp_path, "a", T1, {U1: mined(5)})
    b = backup(tmp_path, "b", T1, {U1: mined(7)})
    res = run(db, [a, b])
    by = {r.path.parent.name: r for r in res}
    assert "same date" in by["a"].skipped_reason
    assert by["b"].skipped_reason is None
    assert rows(db) == [(T1, U1, "minecraft:mined", "minecraft:stone", 7)]
    assert "same date" in capsys.readouterr().out


def test_missing_player_in_newer_backup_keeps_value(tmp_path, db):
    b1 = backup(tmp_path, "b1", T1, {U1: mined(5), U2: mined(8)})
    b2 = backup(tmp_path, "b2", T2, {U1: mined(6)})
    b3 = backup(tmp_path, "b3", T3, {U1: mined(6), U2: mined(8)})
    run(db, [b1, b2, b3])
    assert db.execute("SELECT at, value FROM stat_snapshots WHERE uuid=? ORDER BY at",
                      (U2,)).fetchall() == [(T1, 8)]
    assert db.execute("SELECT COUNT(*) FROM stat_snapshots WHERE value=0").fetchone()[0] == 0


def test_rerun_skips_imported(tmp_path, db):
    bs = [backup(tmp_path, "b1", T1, {U1: mined(5)}), backup(tmp_path, "b2", T2, {U1: mined(6)})]
    run(db, bs)
    before = (rows(db), db.execute("SELECT COUNT(*) FROM snapshot_runs").fetchone()[0],
              db.execute("SELECT COUNT(*) FROM imports").fetchone()[0])
    res = run(db, bs)
    assert all("already imported" in r.skipped_reason for r in res)
    assert (rows(db), db.execute("SELECT COUNT(*) FROM snapshot_runs").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM imports").fetchone()[0]) == before


def counts(conn):
    return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("stat_snapshots", "scoreboard_snapshots", "snapshot_runs",
                      "imports", "players")]


def test_dry_run_writes_nothing(tmp_path, db):
    b1 = backup(tmp_path, "b1", T1, {U1: mined(5)},
                scoreboard=(["digs"], [("Alice", "digs", 3)]), usercache={U1: "Alice"})
    b2 = backup(tmp_path, "b2", T2, {U1: mined(6), U2: mined(1)})
    before = counts(db)
    res = run(db, [b1, b2], dry_run=True)
    assert counts(db) == before
    assert not db.in_transaction
    # what would be written, change-only across the dry run too
    assert [(r.players, r.rows_stats, r.rows_scoreboard) for r in res] == [(1, 1, 1), (2, 2, 0)]


def test_busy_db_aborts(tmp_path, world, db):
    other = sqlite3.connect(world.db_path)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ib.DatabaseBusy, match="a snapshot is running"):
            run(db, [backup(tmp_path, "b", T1, {U1: mined(5)})], lock_timeout_s=0.1)
    finally:
        other.rollback()
        other.close()
    assert counts(db)[0] == 0


def test_mirror_objectives_skipped(tmp_path, db):
    sb = (["mined_stone", "digs", "m-grass_path", "kb-old_mob", "old_mirror"],
          [("Alice", "mined_stone", 5), ("Alice", "digs", 2),
           ("Alice", "m-grass_path", 4), ("Alice", "kb-old_mob", 1),
           ("Alice", "old_mirror", 9), ("luigiserectcock", "digs", 7)])
    db.execute("INSERT INTO scoreboard_meta (server_key, objective, is_vanilla_mirror, "
               "first_seen, last_seen, removed) VALUES ('smp','old_mirror',1,1,1,1)")
    db.commit()
    b = backup(tmp_path, "b", T1, {U1: mined(5)}, scoreboard=sb, usercache={U1: "Alice"})
    [r] = run(db, [b], dictionary={"minecraft.mined:minecraft.stone": "mined_stone"})
    got = db.execute("SELECT participant_kind, uuid, raw_name, objective, value "
                     "FROM scoreboard_snapshots ORDER BY objective, raw_name").fetchall()
    assert got == [("player", U1, "Alice", "digs", 2),
                   ("unresolved", None, "luigiserectcock", "digs", 7)]
    assert r.mirrors_by_dictionary == 2  # mined_stone (dictionary) + old_mirror (meta)
    assert r.mirrors_by_prefix == ["kb-old_mob", "m-grass_path"]
    assert r.objectives == 5


def test_keep_prefixed_imports_prefixed_objectives(tmp_path, db):
    sb = (["m-grass_path", "digs"], [("Alice", "m-grass_path", 4), ("Alice", "digs", 2)])
    b = backup(tmp_path, "b", T1, {U1: mined(5)}, scoreboard=sb, usercache={U1: "Alice"})
    [r] = run(db, [b], keep_prefixed=True)
    assert sorted(o for (o,) in db.execute("SELECT objective FROM scoreboard_snapshots")) == [
        "digs", "m-grass_path"]
    assert r.mirrors_by_prefix == []


def test_extra_dictionary_counts_as_dictionary(tmp_path, db):
    sb = (["m-grass_path", "digs"], [("Alice", "m-grass_path", 4), ("Alice", "digs", 2)])
    b = backup(tmp_path, "b", T1, {U1: mined(5)}, scoreboard=sb, usercache={U1: "Alice"})
    extra = tmp_path / "dictionary-1.16.5.json"
    extra.write_text(json.dumps({"minecraft.mined:minecraft.grass_path": "m-grass_path"}),
                     encoding="utf-8")
    merged = ib.merge_dictionaries({"x": "mined_stone"}, [extra])
    [r] = run(db, [b], dictionary=merged)
    assert r.mirrors_by_dictionary == 1 and r.mirrors_by_prefix == []
    assert [o for (o,) in db.execute("SELECT objective FROM scoreboard_snapshots")] == ["digs"]


def test_scoreboard_participant_resolved_via_players(tmp_path, db):
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, 'Bob', 1, 5, 5)", (U2,))
    db.commit()
    b = backup(tmp_path, "b", T1, {U1: mined(5)}, scoreboard=(["digs"], [("Bob", "digs", 3)]))
    run(db, [b])
    assert db.execute("SELECT participant_kind, uuid FROM scoreboard_snapshots").fetchall() == [
        ("player", U2)]


def _sb(conn):
    return sorted(conn.execute(
        "SELECT at, participant_kind, uuid, raw_name, objective, value "
        "FROM scoreboard_snapshots"), key=lambda r: (r[0], r[3], r[4]))


def _renamed_player(db, tmp_path):
    """U1 renamed Rat_ -> Rat (live name Rat); both names on the board in
    both backups. b1's usercache still says Rat_, b2's says Rat."""
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, 'Rat', 1, 5, 5)", (U1,))
    db.commit()
    sb = (["Levels"], [("Rat_", "Levels", 179), ("Rat", "Levels", 7)])
    b1 = backup(tmp_path, "b1", T1, {U1: mined(1)}, scoreboard=sb, usercache={U1: "Rat_"})
    b2 = backup(tmp_path, "b2", T2, {U1: mined(1)}, scoreboard=sb, usercache={U1: "Rat"})
    return b1, b2


# Both names summed into one row under the latest name (Rat), as the in-game
# stattransfer does; T2 has the same sum, so no row (change-only).
RENAME_ROWS = [(T1, "player", U1, "Rat", "Levels", 186)]


def test_renamed_player_names_summed_under_latest_name(tmp_path, db, capsys):
    results = run(db, list(_renamed_player(db, tmp_path)))
    assert _sb(db) == RENAME_ROWS
    assert [r.renamed_players for r in results] == [{("Rat_", "Rat")}] * 2
    ib._print_renamed(results[0])
    assert "Rat_ -> Rat" in capsys.readouterr().out
    # never two rows for the same (uuid, objective, at)
    assert db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM scoreboard_snapshots "
                      "WHERE uuid IS NOT NULL GROUP BY at, uuid, objective "
                      "HAVING COUNT(*) > 1)").fetchone() == (0,)


def test_old_name_only_on_board_goes_to_latest_name(tmp_path, db):
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, 'Rat', 1, 5, 5)", (U1,))
    db.commit()
    b = backup(tmp_path, "b", T1, {U1: mined(1)}, usercache={U1: "Rat_"},
               scoreboard=(["Levels", "Xp"], [("Rat_", "Levels", 179), ("Rat_", "Xp", 5),
                                              ("npc", "Xp", 1)]))
    run(db, [b])
    assert _sb(db) == [(T1, "player", U1, "Rat", "Levels", 179),
                       (T1, "player", U1, "Rat", "Xp", 5),
                       (T1, "unresolved", None, "npc", "Xp", 1)]


def test_reimport_scoreboards_repairs_old_rows(tmp_path, db):
    run(db, list(_renamed_player(db, tmp_path)))
    stat_rows = rows(db)
    runs = db.execute("SELECT at, rows_written FROM snapshot_runs WHERE kind='import' "
                      "ORDER BY at").fetchall()
    # what the old code wrote: one row per name, both as U1, at each point
    db.execute("DELETE FROM scoreboard_snapshots")
    db.executemany(
        "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
        "objective, value) VALUES (?, 'smp', 'player', ?, ?, 'Levels', ?)",
        [(T1, U1, "Rat_", 179), (T1, U1, "Rat", 7), (T2, U1, "Rat_", 179), (T2, U1, "Rat", 7)])
    db.execute("UPDATE snapshot_runs SET rows_written = rows_written + "
               "CASE WHEN at=? THEN 1 ELSE 2 END WHERE kind='import'", (T1,))
    db.commit()
    backups, errors = ib.imported_backups(db, "smp")
    assert errors == [] and [b.at for b in backups] == [T1, T2]

    res = ib.reimport_scoreboards(db, "smp", backups, set(), dry_run=True)
    assert [(b.at, d, w) for b, d, w, _ in res] == [(T1, 2, 1), (T2, 2, 0)]
    assert res[0][3] == {("Rat_", "Rat")} and res[1][3] == {("Rat_", "Rat")}
    assert len(_sb(db)) == 4  # dry run: unchanged

    ib.reimport_scoreboards(db, "smp", backups, set(), dry_run=False)
    assert _sb(db) == RENAME_ROWS
    assert rows(db) == stat_rows
    assert db.execute("SELECT at, rows_written FROM snapshot_runs WHERE kind='import' "
                      "ORDER BY at").fetchall() == runs


def test_reimport_scoreboards_cli_needs_backup_folders(tmp_path, db, world, capsys):
    run(db, list(_renamed_player(db, tmp_path)))
    db.execute("UPDATE imports SET world_path=?", (str(tmp_path / "gone"),))
    db.commit()
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(world.config), encoding="utf-8")
    before = _sb(db)
    assert ib.main(["--server", "smp", "--config", str(cfg), "--reimport-scoreboards",
                    "--no-backup"], dictionary={}) == 1
    assert "nothing changed" in capsys.readouterr().out
    assert _sb(db) == before
    assert ib.main(["--server", "smp", "--config", str(cfg), "--reimport-scoreboards",
                    str(tmp_path)], dictionary={}) == 2


def test_scoreboard_change_only(tmp_path, db):
    b1 = backup(tmp_path, "b1", T1, {}, scoreboard=(["digs"], [("npc", "digs", 3)]))
    b2 = backup(tmp_path, "b2", T2, {}, scoreboard=(["digs"], [("npc", "digs", 3)]))
    b3 = backup(tmp_path, "b3", T3, {}, scoreboard=(["digs"], [("npc", "digs", 4)]))
    assert [r.rows_scoreboard for r in run(db, [b1, b2, b3])] == [1, 0, 1]


def test_unknown_names_filled_not_overwritten(tmp_path, db):
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, NULL, 0, 900, 950)", (U1,))
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, 'NewName', 1, 900, 950)", (U2,))
    db.commit()
    b1 = backup(tmp_path, "b1", T1, {U1: mined(1), U2: mined(1), U3: mined(1)},
                usercache={U1: "OldAlice", U2: "OldName", U3: "Carol0"})
    b2 = backup(tmp_path, "b2", T2, {U1: mined(2), U3: mined(2)},
                usercache={U1: "Alice", U3: "Carol"})
    run(db, [b1, b2])
    got = {u: r for u, *r in db.execute(
        "SELECT uuid, name, name_resolved, first_seen, last_observed FROM players")}
    assert got[U1] == ["Alice", 1, 900, 950]      # NULL filled with the newest backup name
    assert got[U2] == ["NewName", 1, 900, 950]    # never overwritten
    assert got[U3] == ["Carol", 1, T1, T1]        # inserted at its first backup


def checksum(conn, table):
    h = hashlib.sha256()
    for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4"):
        h.update(repr(r).encode())
    return h.hexdigest()


def test_live_current_tables_untouched(tmp_path, world, run_snapshot):
    world.add_stats("smp", U1, mined(50))
    world.set_usercache("smp", {U1: "Alice"})
    world.set_scoreboard("smp", ["digs"], [("Alice", "digs", 9)], {"t": ["Alice"]})
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    before = (checksum(conn, "stat_current"), checksum(conn, "scoreboard_current"),
              checksum(conn, "scoreboard_meta"))
    b = backup(tmp_path, "b", T1, {U1: mined(5), U2: mined(3)},
               scoreboard=(["digs", "old"], [("Alice", "digs", 2), ("Alice", "old", 1)]),
               usercache={U1: "Alice"})
    [r] = run(conn, [b])
    assert r.rows_stats == 2 and r.rows_scoreboard == 2
    assert (checksum(conn, "stat_current"), checksum(conn, "scoreboard_current"),
            checksum(conn, "scoreboard_meta")) == before
    conn.close()


def test_unknown_keys_reported_and_skip_flag(tmp_path, world, run_snapshot):
    world.add_stats("smp", U1, mined(50))
    run_snapshot(LIVE)
    conn = snapshot.connect_db(world.db_path)
    st = {U1: {"minecraft:mined": {"minecraft:stone": 5, "minecraft:weird_block": 2,
                                   "minecraft:grass": 1, "minecraft:dirt_path": 4}}}
    [r] = run(conn, [backup(tmp_path, "b1", T1, st)], dry_run=True)
    assert r.unknown_keys == {"minecraft:mined/minecraft:weird_block"}
    assert r.rows_stats == 4  # dirt_path: a rename target, so known
    [r] = run(conn, [backup(tmp_path, "b2", T1, st)], skip_unknown=True)
    assert r.unknown_keys == {"minecraft:mined/minecraft:weird_block"}
    assert r.rows_stats == 3
    assert conn.execute("SELECT COUNT(*) FROM stat_snapshots WHERE stat_key="
                        "'minecraft:weird_block'").fetchone()[0] == 0
    conn.close()


def test_point_reads_are_pinned(db):
    for sql in (ib.PREV_STATS_SQL, ib.PREV_SCOREBOARD_SQL):
        plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, ("smp", T1)))
        assert "idx_stat_point" in plan or "idx_scoreboard_point" in plan


def test_load_renames_ignores_comment_keys(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"_versions": {"a": "1.17"}, "*": {"a": "b"}}), encoding="utf-8")
    assert ib.load_renames(p) == {"*": {"a": "b"}}


def test_shipped_rename_table():
    r = ib.load_renames(ib.RENAMES_PATH)
    assert r["minecraft:custom"]["minecraft:play_one_minute"] == "minecraft:play_time"
    assert r["*"]["minecraft:grass_path"] == "minecraft:dirt_path"
    assert r["*"]["minecraft:grass"] == "minecraft:short_grass"


# ------------------------------------------------------------------ CLI

def write_config(tmp_path, world):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(world.config), encoding="utf-8")
    return p


def test_db_backup_created(tmp_path, world, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    rc = ib.main(["--server", "smp", "--config", str(write_config(tmp_path, world)), str(w)],
                 dictionary={})
    assert rc == 0
    made = list(Path(world.db_path).parent.glob("pre-import-*.db"))
    assert len(made) == 1
    old = sqlite3.connect(made[0])
    assert old.execute("SELECT COUNT(*) FROM stat_snapshots").fetchone()[0] == 0
    old.close()
    assert db.execute("SELECT COUNT(*) FROM stat_snapshots").fetchone()[0] == 1


def test_no_backup_flag(tmp_path, world, db):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    rc = ib.main(["--server", "smp", "--no-backup", "--config",
                  str(write_config(tmp_path, world)), str(w)], dictionary={})
    assert rc == 0
    assert list(Path(world.db_path).parent.glob("pre-import-*.db")) == []
    assert db.execute("SELECT COUNT(*) FROM stat_snapshots").fetchone()[0] == 1


def test_main_dry_run_date_override_and_output(tmp_path, world, db, capsys, monkeypatch):
    import snapshot
    # no bundled dictionaries here, so the prefix fallback is what catches m-grass_path
    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", tmp_path / "no-dictionaries")
    w = make_backup(tmp_path, name="b1", last_played_ms=None,
                    stats={U1: {"minecraft:custom": {"minecraft:play_one_minute": 7}}},
                    scoreboard=(["digs", "m-grass_path"], [("npc", "digs", 1),
                                                           ("npc", "m-grass_path", 1)]))
    rc = ib.main(["--server", "smp", "--dry-run", "--config",
                  str(write_config(tmp_path, world)), "--date", f"{w}=2020-09-13", str(w)],
                 dictionary={})
    out = capsys.readouterr().out
    assert rc == 0
    assert "2020-09-13" in out and "dry run" in out.lower()
    assert "probable mirrors skipped: 1" in out and "m-grass_path" in out
    assert "minecraft:play_one_minute" in out
    assert counts(db)[0] == 0
    assert list(Path(world.db_path).parent.glob("pre-import-*.db")) == []


def test_main_refused_backup_reported(tmp_path, world, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=None, stats={U1: mined(1)})
    rc = ib.main(["--server", "smp", "--config", str(write_config(tmp_path, world)), str(w)],
                 dictionary={})
    assert rc == 1
    assert "refused" in capsys.readouterr().out


def test_main_calls_rebuild(tmp_path, world, db, monkeypatch):
    import derive_achievements
    calls = []
    monkeypatch.setattr(derive_achievements, "rebuild_from_points",
                        lambda conn, key, settings: calls.append(key) or {})
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    cfg = str(write_config(tmp_path, world))
    assert ib.main(["--server", "smp", "--no-backup", "--dry-run", "--config", cfg,
                    str(w)], dictionary={}) == 0
    assert calls == []
    assert ib.main(["--server", "smp", "--no-backup", "--config", cfg, str(w)],
                   dictionary={}) == 0
    assert calls == ["smp"]


def test_boat_entity_renamed_only_in_entity_categories(tmp_path, db):
    shipped = ib.load_renames(ib.RENAMES_PATH)
    b = backup(tmp_path, "b", T1, {U1: {
        "minecraft:killed": {"minecraft:boat": 3, "minecraft:chest_boat": 1},
        "minecraft:killed_by": {"minecraft:boat": 2},
        "minecraft:used": {"minecraft:oak_boat": 5},
        "minecraft:mined": {"minecraft:chain": 4}}}, version_name="1.21.1")
    ib.import_backups(db, "smp", [b], shipped, {}, False)
    assert rows(db) == [
        (T1, U1, "minecraft:killed", "minecraft:oak_boat", 3),
        (T1, U1, "minecraft:killed", "minecraft:oak_chest_boat", 1),
        (T1, U1, "minecraft:killed_by", "minecraft:oak_boat", 2),
        (T1, U1, "minecraft:mined", "minecraft:iron_chain", 4),
        (T1, U1, "minecraft:used", "minecraft:oak_boat", 5)]
    assert "minecraft:boat" not in shipped["*"] and "minecraft:potion" not in shipped["*"]


def test_bundled_dictionaries_catch_old_mirror_names(tmp_path, world, db, capsys, monkeypatch):
    import snapshot
    d = tmp_path / "dicts"
    d.mkdir()
    (d / "dictionary-1.16.5.json").write_text('{"m-grass_path": "m-grass_path"}', encoding="utf-8")
    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", d)
    w = make_backup(tmp_path, name="b1", last_played_ms=None,
                    stats={U1: {"minecraft:custom": {"minecraft:play_one_minute": 7}}},
                    scoreboard=(["digs", "m-grass_path"], [("npc", "digs", 1),
                                                           ("npc", "m-grass_path", 1)]))
    rc = ib.main(["--server", "smp", "--dry-run", "--config",
                  str(write_config(tmp_path, world)), "--date", f"{w}=2020-09-13", str(w)],
                 dictionary={})
    out = capsys.readouterr().out
    assert rc == 0
    assert "mirrors skipped by dictionary: 1" in out
    assert "probable mirrors skipped: 1" not in out


def test_fetch_dictionary_prefers_local_file(tmp_path, monkeypatch):
    import snapshot
    d = tmp_path / "dicts"
    d.mkdir()
    (d / "dictionary-9.9.json").write_text('{"m-x": "m-x"}', encoding="utf-8")
    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", d)
    def no_network(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(snapshot.urllib.request, "urlopen", no_network)
    conn = snapshot.connect_db(str(tmp_path / "t.db"))
    try:
        assert snapshot.fetch_dictionary(conn, "9.9") == {"m-x": "m-x"}
    finally:
        conn.close()


# ------------------------------------------------------------------ Task 3 carried fixes

def test_main_backup_after_live_start_counts_as_refused(tmp_path, world, run_snapshot, capsys):
    world.add_stats("smp", U1, mined(5))
    run_snapshot(LIVE)
    late = make_backup(tmp_path, name="late", last_played_ms=(LIVE + 10) * 1000,
                       stats={U1: mined(1)})
    ok = make_backup(tmp_path, name="ok", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    rc = ib.main(["--server", "smp", "--no-backup", "--config",
                  str(write_config(tmp_path, world)), str(late), str(ok)], dictionary={})
    out = capsys.readouterr().out
    assert rc == 1
    assert "on or after" in out
    assert "1 imported" in out and "1 refused" in out


def _exit2(capsys, argv, needle):
    rc = ib.main(argv, dictionary={})
    out = capsys.readouterr()
    assert rc == 2
    assert needle in out.out
    assert "Traceback" not in out.out + out.err


def test_main_bad_date_exits_2(tmp_path, world, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    _exit2(capsys, ["--server", "smp", "--config", str(write_config(tmp_path, world)),
                    "--date", f"{w}=2021-13-45", str(w)], "--date")


def test_main_date_for_unlisted_dir_exits_2(tmp_path, world, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    other = tmp_path / "elsewhere"
    _exit2(capsys, ["--server", "smp", "--config", str(write_config(tmp_path, world)),
                    "--date", f"{other}=2021-01-01", str(w)], "not among")


def test_main_missing_config_exits_2(tmp_path, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    _exit2(capsys, ["--server", "smp", "--config", str(tmp_path / "nope.json"), str(w)],
           "config")


def test_main_config_without_stats_tracking_exits_2(tmp_path, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"server": {}}), encoding="utf-8")
    _exit2(capsys, ["--server", "smp", "--config", str(p), str(w)], "stats_tracking")


def test_main_server_not_in_db_exits_2(tmp_path, world, capsys):
    # the config knows smp, but the DB was never synced (no snapshot ran yet)
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(1)})
    _exit2(capsys, ["--server", "smp", "--config", str(write_config(tmp_path, world)),
                    str(w)], "not in the DB")


def test_same_world_path_other_date_skipped(tmp_path, db, capsys):
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    run(db, [ib.read_backup(w, None)])
    before = (rows(db), counts(db))
    [r] = run(db, [ib.read_backup(w, T2)])  # same folder, --date moved it
    assert "already imported" in r.skipped_reason
    assert "[warn]" in capsys.readouterr().out
    assert (rows(db), counts(db)) == before


def test_nothing_to_import_skips_db_backup_and_rebuild(tmp_path, world, db, monkeypatch):
    import derive_achievements
    calls = []
    monkeypatch.setattr(derive_achievements, "rebuild_from_points",
                        lambda conn, key, settings, **kw: calls.append(key) or {})
    w = make_backup(tmp_path, name="b1", last_played_ms=T1 * 1000, stats={U1: mined(5)})
    cfg = str(write_config(tmp_path, world))
    assert ib.main(["--server", "smp", "--no-backup", "--config", cfg, str(w)],
                   dictionary={}) == 0
    assert calls == ["smp"]
    assert ib.main(["--server", "smp", "--config", cfg, str(w)], dictionary={}) == 0
    assert calls == ["smp"]  # no rebuild the second time
    assert list(Path(world.db_path).parent.glob("pre-import-*.db")) == []


def test_rename_versions_scute():
    raw = json.loads(ib.RENAMES_PATH.read_text(encoding="utf-8"))
    assert raw["_versions"]["minecraft:scute"] == "1.21.5"


def test_other_folder_same_date_as_imported_warns(tmp_path, db, capsys):
    run(db, [backup(tmp_path, "b1", T1, {U1: mined(5)})])
    capsys.readouterr()
    [r] = run(db, [backup(tmp_path, "copy", T1, {U1: mined(9)})])
    assert "different folder" in r.skipped_reason
    assert "[warn]" in capsys.readouterr().out
    assert rows(db) == [(T1, U1, "minecraft:mined", "minecraft:stone", 5)]
