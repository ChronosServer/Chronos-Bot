"""Fixes after the round-2 deploy (2026-11-26):
- case-sensitive renames (NotAless50 is a former name; the Carpet bot
  `notaless50` is someone else),
- the split first live run (rows at F, run recorded at L a few seconds later)
  no longer makes a fold spike,
- a scoreboard name unknown to its server's usercache resolves through the
  other servers' usercaches (Algi_ on SMP after a stattransfer), and its
  unresolved live rows are folded in once it resolves."""
import sqlite3

import pytest

from api import merges as mg

A = "43c80944-4099-43e3-948a-5f183a1ca0d4"   # Algi_ / Alugia7
B = "76e49874-1786-4d4c-9527-4dfd741c989d"   # IBeatLIMBO


def q(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def conn_of(world):
    c = sqlite3.connect(world.db_path)
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------- case-sensitive renames

def exact_rename():
    return mg.parse_merges({"renames": [
        {"uuid": B, "names": ["NotAless50"], "case_sensitive": True}]})


def test_case_sensitive_rename_matches_exact_name_only():
    m = exact_rename()
    assert m.rename_target("NotAless50") == B
    assert m.rename_target("notaless50") is None
    assert m.former_names(B) == ["NotAless50"]


def test_case_sensitive_must_be_bool():
    with pytest.raises(ValueError, match="case_sensitive"):
        mg.parse_merges({"renames": [{"uuid": B, "names": ["x"], "case_sensitive": "yes"}]})


def test_case_sensitive_fold_leaves_the_bot_name(world, run_snapshot):
    world.set_usercache("smp", {B: "IBeatLIMBO"})
    world.add_stats("smp", B, {"minecraft:mined": {"minecraft:stone": 1}})
    world.set_scoreboard("smp", ["digs"], [("IBeatLIMBO", "digs", 10), ("NotAless50", "digs", 7),
                                           ("notaless50", "digs", 3)], {"t": ["IBeatLIMBO"]})
    run_snapshot(1000)
    run_snapshot(2000, merges=exact_rename())
    assert q(world, "SELECT participant_key, value FROM scoreboard_current ORDER BY 1") == [
        (B, 17), ("unresolved:notaless50", 3)]
    assert q(world, "SELECT DISTINCT raw_name FROM scoreboard_snapshots WHERE uuid IS NULL") == [
        ("notaless50",)]


# ---------------------------------------------------------------- split first run

def test_collapse_split_first_run(world, run_snapshot):
    import history_fill
    run_snapshot(1)  # schema + servers
    c = conn_of(world)
    rows = [(997, "a", 30), (1000, "a", 10),        # spike at F, real value at L
            (997, "b", 5),                           # only at F: untouched
            (1000, "c", 0),                          # only at L (fill 0): untouched
            (900, "d", 8), (1000, "d", 2)]           # F outside the window: untouched
    c.executemany("INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, "
                  "raw_name, objective, value) VALUES (?, 'smp', 'unresolved', NULL, ?, 'digs', ?)",
                  rows)
    assert history_fill.collapse_split_first_run(c, "smp", 1000) == 1
    assert c.execute("SELECT at, raw_name, value FROM scoreboard_snapshots WHERE objective='digs' "
                     "ORDER BY raw_name, at").fetchall() == [
        (997, "a", 10), (997, "b", 5), (1000, "c", 0), (900, "d", 8), (1000, "d", 2)]
    assert history_fill.collapse_split_first_run(c, "smp", 1000) == 0
    c.close()


def test_rename_fold_on_a_split_first_run_has_no_spike(world, run_snapshot):
    """As on the live DB: the first live run's rows at F=997, the run recorded
    at L=1000, the old name's 0 (history fill) at L."""
    import merge_players
    world.set_usercache("smp", {B: "IBeatLIMBO"})
    world.add_stats("smp", B, {"minecraft:mined": {"minecraft:stone": 1}})
    world.set_scoreboard("smp", ["digs"], [("IBeatLIMBO", "digs", 10)], {"t": ["IBeatLIMBO"]})
    run_snapshot(1000)
    c = conn_of(world)
    c.execute("UPDATE scoreboard_snapshots SET at=997 WHERE at=1000")
    c.executemany("INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, "
                  "raw_name, objective, value) VALUES (?, 'smp', 'unresolved', NULL, "
                  "'NotAless50', 'digs', ?)", [(500, 7), (1000, 0)])
    c.commit()
    merge_players.apply_pending_merges(c, exact_rename(), 2000, world.db_path, backup=False)
    assert c.execute("SELECT at, value FROM scoreboard_snapshots WHERE uuid=? AND objective='digs' "
                     "ORDER BY at", (B,)).fetchall() == [(500, 7), (997, 10)]
    c.close()


def test_snapshot_collapses_existing_split_once(world, run_snapshot):
    world.set_usercache("smp", {B: "IBeatLIMBO"})
    world.add_stats("smp", B, {"minecraft:mined": {"minecraft:stone": 1}})
    world.set_scoreboard("smp", ["digs"], [("IBeatLIMBO", "digs", 10)], {"t": ["IBeatLIMBO"]})
    run_snapshot(1000)
    c = conn_of(world)
    c.execute("UPDATE scoreboard_snapshots SET at=997, value=17 WHERE at=1000 AND uuid=?", (B,))
    c.execute("INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
              "objective, value) VALUES (1000, 'smp', 'player', ?, 'IBeatLIMBO', 'digs', 10)", (B,))
    c.execute("DELETE FROM meta WHERE key='first_run_split_collapsed'")
    c.commit()
    c.close()
    run_snapshot(2000)
    assert q(world, "SELECT at, value FROM scoreboard_snapshots WHERE uuid=? AND objective='digs' "
                    "ORDER BY at", B) == [(997, 10)]


# ---------------------------------------------------------------- names from other servers

@pytest.fixture
def algi(world, run_snapshot):
    """SMP knows the account only as Alugia7; CMP's usercache has Algi_."""
    world.set_usercache("smp", {A: "Alugia7"})
    world.add_stats("smp", A, {"minecraft:mined": {"minecraft:stone": 1}})
    return world


def test_scoreboard_name_resolves_through_another_servers_usercache(algi, run_snapshot):
    algi.set_usercache("cmp", {A: "Algi_"})
    algi.set_scoreboard("smp", ["digs"], [("Algi_", "digs", 50)], {"t": ["Alugia7"]})
    run_snapshot(1000)
    assert q(algi, "SELECT participant_key, value FROM scoreboard_current") == [(A, 50)]


def test_unresolved_name_is_folded_once_it_resolves(algi, run_snapshot):
    algi.set_scoreboard("smp", ["digs"], [("Alugia7", "digs", 30)], {"t": ["Alugia7"]})
    run_snapshot(1000)
    # stattransfer Alugia7 -> Algi_, while no usercache knows Algi_ yet
    algi.set_scoreboard("smp", ["digs"], [("Algi_", "digs", 31)], {"t": ["Alugia7"]})
    run_snapshot(2000)
    assert q(algi, "SELECT participant_key, value FROM scoreboard_current ORDER BY 1") == [
        (A, 0), ("unresolved:Algi_", 31)]
    algi.set_usercache("cmp", {A: "Algi_"})  # joins a CMP: now known
    run_snapshot(3000)
    assert q(algi, "SELECT participant_key, value FROM scoreboard_current") == [(A, 31)]
    assert q(algi, "SELECT at, value FROM scoreboard_snapshots WHERE uuid=? ORDER BY at", A) == [
        (1000, 30), (2000, 31)]
    assert q(algi, "SELECT COUNT(*) FROM scoreboard_snapshots WHERE uuid IS NULL") == [(0,)]


def test_fold_leaves_rows_from_before_live_tracking(algi, run_snapshot):
    """Backup-era rows of a name stay as they are (a name can change hands;
    old names need an explicit rename entry)."""
    algi.set_scoreboard("smp", ["digs"], [("Algi_", "digs", 31)], {"t": ["Alugia7"]})
    run_snapshot(1000)
    c = conn_of(algi)
    c.execute("INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
              "objective, value) VALUES (10, 'smp', 'unresolved', NULL, 'Algi_', 'old', 99)")
    c.commit()
    c.close()
    algi.set_usercache("cmp", {A: "Algi_"})
    run_snapshot(2000)
    assert q(algi, "SELECT at, raw_name, value FROM scoreboard_snapshots WHERE uuid IS NULL") == [
        (10, "Algi_", 99)]
    assert q(algi, "SELECT participant_key, value FROM scoreboard_current "
                   "WHERE objective='digs'") == [(A, 31)]
