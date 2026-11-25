"""player_merges.json `renames`: a uuid's former scoreboard names (renames of
one account; scoreboards key by name)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache, merges as mg
from api.main import create_app

U = "bb8d717d-1e2f-4204-8ea5-88c90a60bca4"   # NotQuiteRat
V = "5f2a15be-cdcc-49a6-a74a-1b31e0562786"   # Diggus
OLD = "c273fdfb-2181-47be-b174-6d8f20489b1b"  # merged-away account
NEW = "2099-01-01 00:00:00 +0000"
D2021 = 1635426651      # 2021-10-28
D2023 = 1702755455      # 2023-12-16


def raw(**kw):
    base = {"merges": [{"from": OLD, "to": V, "names": ["OldAcc_"]}],
            "renames": [{"uuid": U, "names": ["NotQuiteRat_"]}]}
    base.update(kw)
    return base


def test_parse_renames_and_former_names():
    m = mg.parse_merges(raw())
    assert m.rename_target("notquiterat_", D2021) == U
    assert m.rename_target("NotQuiteRat_", D2023) == U
    assert m.rename_target("someoneelse", D2021) is None
    assert m.former_names(U) == ["NotQuiteRat_"]
    assert m.former_names(V) == ["OldAcc_"]
    assert m.merged_from(V) == [OLD]
    assert m.former_names(OLD) == []
    assert bool(mg.parse_merges({"renames": [{"uuid": U, "names": ["x"]}]}))


def test_rename_date_bounds():
    m = mg.parse_merges(raw(renames=[
        {"uuid": U, "names": ["Shared"], "from": "2021-01-01", "until": "2021-12-31"},
        {"uuid": V, "names": ["Shared"], "from": "2022-01-01"}]))
    assert m.rename_target("shared", D2021) == U
    assert m.rename_target("shared", D2023) == V
    assert m.rename_target("shared", 1609459199) is None  # 2020-12-31 23:59:59
    assert m.rename_target("shared", 1640995199) == U     # 2021-12-31 23:59:59 (until inclusive)
    assert m.rename_target("shared") in (U, V)            # no date: any entry


@pytest.mark.parametrize("bad", [
    {"renames": [{"uuid": "nope", "names": ["a"]}]},
    {"renames": [{"uuid": U, "names": []}]},
    {"renames": [{"uuid": U, "names": ["a"], "from": "2021-13-01"}]},
    {"renames": [{"uuid": U, "names": ["a"], "from": "2022-01-01", "until": "2021-01-01"}]},
    {"renames": [{"uuid": U, "names": ["OldAcc_"]}],
     "merges": [{"from": OLD, "to": V, "names": ["OldAcc_"]}]},
    {"renames": [{"uuid": U, "names": ["a"]}, {"uuid": V, "names": ["A"]}]},
    {"renames": "x"},
])
def test_parse_rename_errors(bad):
    with pytest.raises(ValueError):
        mg.parse_merges(bad)


def test_same_uuid_twice_is_fine():
    m = mg.parse_merges({"renames": [{"uuid": U, "names": ["a"]}, {"uuid": U, "names": ["b"]}]})
    assert m.former_names(U) == ["a", "b"]


@pytest.fixture
def api(world, run_snapshot, monkeypatch, tmp_path):
    world.set_usercache("cmp", {U: "NotQuiteRat", V: "Diggus", OLD: "OldAcc_"})
    world.add_stats("cmp", U, {"minecraft:mined": {"minecraft:stone": 5}})
    world.add_stats("cmp", V, {"minecraft:mined": {"minecraft:stone": 6}})
    world.add_stats("cmp", OLD, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(raw()), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    mg.reset_api_merges()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def test_player_aka_and_old_name_lookup(api):
    j = api.get("/api/stats/player/NotQuiteRat_").json()
    assert j["uuid"] == U and j["aka"] == ["NotQuiteRat_"]
    j = api.get(f"/api/stats/player/{V}").json()
    assert j["aka"] == ["OldAcc_"]


def test_blocked_former_name(world, run_snapshot):
    from api import names
    names.BLOCKLIST_PATH.write_text("notquite\n", encoding="utf-8")
    world.set_usercache("cmp", {V: "Diggus"})
    world.add_stats("cmp", V, {"minecraft:mined": {"minecraft:stone": 6}})
    run_snapshot(1000, merges=mg.parse_merges({"renames": [{"uuid": V, "names": ["NotQuite_"]}]}))
    c = sqlite3.connect(world.db_path)
    assert c.execute("SELECT uuid, name FROM blocked_players").fetchall() == [(V, "NotQuite_")]
    c.close()


# ------------------------------------------------------------- fold + resolution

def rename(**kw):
    return mg.parse_merges({"renames": [dict({"uuid": U, "names": ["NotQuiteRat_"]}, **kw)]})


def q(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


@pytest.fixture
def renamed_history(world, run_snapshot):
    """NotQuiteRat (U) and her old name's unresolved scores, as on the live DB."""
    world.set_usercache("smp", {U: "NotQuiteRat"})
    world.add_stats("smp", U, {"minecraft:mined": {"minecraft:stone": 1}})

    def board(scores):
        world.set_scoreboard("smp", ["digs"], scores, {"t": ["NotQuiteRat"]})
    board([("NotQuiteRat", "digs", 13132), ("NotQuiteRat_", "digs", 1334970)])
    run_snapshot(1000)
    board([("NotQuiteRat", "digs", 13132), ("NotQuiteRat_", "digs", 9371069)])
    run_snapshot(100_000)
    board([("NotQuiteRat", "digs", 10304631)])  # stattransfer: old name reset
    run_snapshot(200_000)
    world.board = board
    return world


def test_rename_fold_sums_unresolved_rows(renamed_history, run_snapshot):
    w = renamed_history
    run_snapshot(300_000, merges=rename())
    assert q(w, "SELECT at, value FROM scoreboard_snapshots WHERE uuid=? AND objective='digs' "
                "ORDER BY at", U) == [(1000, 1348102), (100_000, 9384201), (200_000, 10304631)]
    assert q(w, "SELECT COUNT(*) FROM scoreboard_snapshots WHERE uuid IS NULL") == [(0,)]
    assert q(w, "SELECT participant_key, value FROM scoreboard_current") == [(U, 10304631)]
    assert q(w, "SELECT value FROM meta WHERE key=?", f"renamed:{U}:notquiterat_") == [("1",)]
    before = q(w, "SELECT * FROM scoreboard_snapshots ORDER BY id")
    run_snapshot(400_000, merges=rename())
    assert q(w, "SELECT * FROM scoreboard_snapshots ORDER BY id") == before


def test_rename_date_bounds_leave_later_rows(renamed_history, run_snapshot):
    w = renamed_history
    run_snapshot(300_000, merges=rename(until="1970-01-01"))  # ends at 86399
    assert q(w, "SELECT at, value FROM scoreboard_snapshots WHERE uuid=? AND objective='digs' "
                "ORDER BY at", U) == [(1000, 1348102), (100_000, 13132), (200_000, 10304631)]
    assert q(w, "SELECT at, value FROM scoreboard_snapshots WHERE uuid IS NULL ORDER BY at") == [
        (100_000, 9371069), (200_000, 0)]


def test_live_snapshot_resolves_rename(world, run_snapshot):
    world.set_usercache("smp", {U: "NotQuiteRat"})
    world.add_stats("smp", U, {"minecraft:mined": {"minecraft:stone": 1}})
    world.set_scoreboard("smp", ["digs"], [("NotQuiteRat", "digs", 10), ("NotQuiteRat_", "digs", 5)],
                         {"t": ["NotQuiteRat"]})
    run_snapshot(1000, merges=rename())
    assert q(world, "SELECT participant_key, value FROM scoreboard_current") == [(U, 15)]


def test_import_resolves_rename(tmp_path, world, monkeypatch):
    import snapshot
    from conftest import make_backup
    from tools import import_backups as ib
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"renames": [{"uuid": V, "names": ["Q1306"]}]}), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    conn = snapshot.connect_db(world.db_path)
    snapshot.sync_servers_table(conn, world.config)
    w = make_backup(tmp_path, name="b", last_played_ms=1_600_000_000_000,
                    stats={V: {"minecraft:mined": {"minecraft:stone": 1}}},
                    scoreboard=(["digs"], [("Q1306", "digs", 7)]), usercache={V: "Diggus"})
    ib.import_backups(conn, "smp", [ib.read_backup(w, None)], {}, {}, False)
    assert conn.execute("SELECT uuid, raw_name, value FROM scoreboard_snapshots").fetchall() == [
        (V, "Diggus", 7)]
    conn.close()


@pytest.fixture
def imported_then_live(tmp_path, world, run_snapshot):
    """Backups imported before the rename was listed: b1 has Q1306=10 (unresolved),
    b2 (after an in-game stattransfer) has Diggus=15 and no Q1306; live Diggus=20."""
    from conftest import make_backup
    from tools import import_backups as ib
    import snapshot
    world.set_usercache("smp", {V: "Diggus"})
    world.add_stats("smp", V, {"minecraft:mined": {"minecraft:stone": 1}})
    world.set_scoreboard("smp", ["digs"], [("Diggus", "digs", 20)], {"t": ["Diggus"]})
    run_snapshot(1_800_000_000)
    stats = {V: {"minecraft:mined": {"minecraft:stone": 1}}}
    b1 = make_backup(tmp_path, name="b1", last_played_ms=1_600_000_000_000, stats=stats,
                     scoreboard=(["digs"], [("Q1306", "digs", 10)]), usercache={V: "Diggus"})
    b2 = make_backup(tmp_path, name="b2", last_played_ms=1_700_000_000_000, stats=stats,
                     scoreboard=(["digs"], [("Diggus", "digs", 15)]), usercache={V: "Diggus"})
    conn = snapshot.connect_db(world.db_path)
    ib.import_backups(conn, "smp", [ib.read_backup(b1, None), ib.read_backup(b2, None)],
                      {}, {}, False)
    conn.close()
    return world


def digs(world):
    return q(world, "SELECT at, value FROM scoreboard_snapshots WHERE uuid=? AND objective='digs' "
                    "ORDER BY at", V)


def test_rename_fold_on_imported_server_warns_to_reimport(imported_then_live, run_snapshot,
                                                         capsys):
    m = mg.parse_merges({"renames": [{"uuid": V, "names": ["Q1306"]}]})
    run_snapshot(1_800_000_900, merges=m)
    out = capsys.readouterr().out
    assert "--server smp --reimport-scoreboards" in out


def test_reimport_after_fold_fixes_absent_old_name(imported_then_live, run_snapshot,
                                                   tmp_path, monkeypatch):
    import snapshot
    from tools import import_backups as ib
    w = imported_then_live
    m = {"renames": [{"uuid": V, "names": ["Q1306"]}]}
    run_snapshot(1_800_000_900, merges=mg.parse_merges(m))
    # the fold alone carries Q1306's 10 past the transfer (no row says it went to 0)
    assert digs(w) == [(1_600_000_000, 10), (1_700_000_000, 25), (1_800_000_000, 20)]
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    conn = snapshot.connect_db(w.db_path)
    backups, errors = ib.imported_backups(conn, "smp")
    assert errors == []
    ib.reimport_scoreboards(conn, "smp", backups, set(), dry_run=False)
    conn.close()
    assert digs(w) == [(1_600_000_000, 10), (1_700_000_000, 15), (1_800_000_000, 20)]


def test_block_uses_merged_accounts_own_names(world, run_snapshot):
    """aka shows a merged-away account's own player name, so the block checks it too."""
    from api import names
    names.BLOCKLIST_PATH.write_text("badword\n", encoding="utf-8")
    world.set_usercache("cmp", {V: "Diggus", OLD: "B4dword_alt"})
    world.add_stats("cmp", V, {"minecraft:mined": {"minecraft:stone": 6}})
    world.add_stats("cmp", OLD, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    run_snapshot(2000, merges=mg.parse_merges({"merges": [{"from": OLD, "to": V}]}))
    blocked = {u for (u,) in q(world, "SELECT uuid FROM blocked_players")}
    assert V in blocked


def test_dated_old_name_does_not_shadow_its_current_holder(world, run_snapshot, monkeypatch,
                                                          tmp_path):
    w2 = "dddddddd-0000-4000-8000-000000000009"
    world.set_usercache("cmp", {U: "NotQuiteRat", w2: "Shared"})
    for u in (U, w2):
        world.add_stats("cmp", u, {"minecraft:mined": {"minecraft:stone": 5}})
    run_snapshot(1000)
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"renames": [{"uuid": U, "names": ["Shared"],
                                          "until": "2021-12-31"}]}), encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    mg.reset_api_merges()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        assert c.get("/api/stats/player/Shared").json()["uuid"] == w2
    cache.clear()
