"""Renamed players in usercache.json: one entry per name, newest wins."""
import json
import sqlite3

import snapshot

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
OLD, NEW = "2026-01-01 00:00:00 +0000", "2026-12-01 00:00:00 +0000"


def write_entries(world, key, entries):
    """entries: [(uuid, name, expiresOn)] in file order."""
    (world.server_root(key) / "usercache.json").write_text(json.dumps(
        [{"name": n, "uuid": u, "expiresOn": e} for u, n, e in entries]), encoding="utf-8")


def q(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def test_load_usercache_newest_entry_wins_not_file_order(world):
    # the old-name entry comes LAST in the file but is older
    write_entries(world, "smp", [(U1, "Rat", NEW), (U1, "Rat_", OLD), (U2, "Bob", OLD)])
    assert snapshot.load_usercache(world.server_root("smp")) == {U1: "Rat", U2: "Bob"}
    assert len(snapshot.load_usercache_entries(world.server_root("smp"))) == 3


def test_display_name_is_newest_across_servers(world, run_snapshot):
    # smp knows the new name; cmp (read after smp) still has only the old one
    write_entries(world, "smp", [(U1, "Rat_", OLD), (U1, "Rat", NEW)])
    write_entries(world, "cmp", [(U1, "Rat_", OLD)])
    world.set_scoreboard("smp", [], [], {"t": ["Rat"]})
    for key in ("smp", "cmp"):
        world.add_stats(key, U1, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    assert q(world, "SELECT name FROM players WHERE uuid=?", U1) == [("Rat",)]


def test_team_check_matches_any_name_of_the_uuid(world, run_snapshot):
    # the team still lists the old name; usercache knows both
    write_entries(world, "smp", [(U1, "Rat_", OLD), (U1, "Rat", NEW), (U2, "Bob", NEW)])
    world.set_scoreboard("smp", [], [], {"t": ["Rat_"]})
    for u in (U1, U2):
        world.add_stats("smp", u, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    assert dict(q(world, "SELECT uuid, is_bot FROM player_flags WHERE server_key='smp'")) == {
        U1: 0, U2: 1}


def test_mojang_refreshes_names_no_live_usercache_knows(world, run_snapshot):
    # U1: renamed Obi81 -> ObiWan8_, in no live usercache (name from an old
    # backup). U2: named by the live usercache: never looked up.
    write_entries(world, "cmp", [(U2, "Bob", NEW)])
    for u in (U1, U2):
        world.add_stats("cmp", u, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    c = sqlite3.connect(world.db_path)
    c.execute("UPDATE players SET name='Obi81', name_resolved=1 WHERE uuid=?", (U1,))
    c.execute("DELETE FROM name_lookups")  # run 1's stub Mojang answered nothing (cached)
    c.commit()
    c.close()
    calls = []

    def fetch(method, url, body):
        calls.append(url)
        if url.endswith(U1.replace("-", "")):
            return 200, json.dumps({"id": U1.replace("-", ""), "name": "ObiWan81_"}).encode()
        return 404, b""
    run_snapshot(2000, mojang_fetch=fetch)
    assert dict(q(world, "SELECT uuid, name FROM players")) == {U1: "ObiWan81_", U2: "Bob"}
    assert not any(u.endswith(U2.replace("-", "")) for u in calls)
    run_snapshot(3000, mojang_fetch=fetch)  # cached for retry_after_days: no new call
    assert len([u for u in calls if u.endswith(U1.replace("-", ""))]) == 1


def test_import_learns_old_name_entries(tmp_path, world):
    from conftest import make_backup
    from tools import import_backups as ib
    conn = snapshot.connect_db(world.db_path)
    snapshot.sync_servers_table(conn, world.config)
    w = make_backup(tmp_path, name="b", last_played_ms=1_600_000_000_000,
                    stats={U1: {"minecraft:mined": {"minecraft:stone": 1}}},
                    scoreboard=(["Levels"], [("LordHamster42", "Levels", 5),
                                             ("hampter42", "Levels", 2)]))
    (w.parent / "usercache.json").write_text(json.dumps([
        {"name": "hampter42", "uuid": U1, "expiresOn": NEW},
        {"name": "LordHamster42", "uuid": U1, "expiresOn": OLD}]), encoding="utf-8")
    b = ib.read_backup(w, None)
    assert b.usercache == {U1: "hampter42"}
    [r] = ib.import_backups(conn, "smp", [b], {}, {}, False)
    assert r.renamed_players == {("LordHamster42", "hampter42")}
    assert conn.execute("SELECT participant_kind, uuid, raw_name, value FROM "
                        "scoreboard_snapshots").fetchall() == [("player", U1, "hampter42", 7)]
    conn.close()
