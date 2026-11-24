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


def test_pre113_format_refused(tmp_path):
    w = make_backup(tmp_path, last_played_ms=T1 * 1000, stats={})
    (w / "stats" / f"{U1}.json").write_text(
        json.dumps({"stat.mineBlock.minecraft.stone": 5}), encoding="utf-8")
    with pytest.raises(ValueError, match="pre-1.13 stats format is not supported"):
        ib.read_backup(w, None)


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
