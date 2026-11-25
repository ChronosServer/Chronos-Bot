"""player_merges.json: separate accounts counted as one player."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import cache, merges as mg
from api.main import create_app
from merge_players import sum_timelines

A = "8d5af53c-11a5-4ea5-aa38-1cea7b746ad8"   # from (LordHam)
B = "b1ac344e-4a2d-4202-ac2d-58d44e167df8"   # to (hampter)
C = "33333333-3333-4333-3333-333333333333"
NEW = "2026-12-01 00:00:00 +0000"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


def merges_for(*names):
    return mg.parse_merges({"merges": [{"from": A, "to": B, "names": list(names)}]})


def q(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


# ------------------------------------------------------------------ parsing

def test_parse_chains_and_errors():
    m = mg.parse_merges({"merges": [{"from": A, "to": B}, {"from": C, "to": A}]})
    assert m.target(C) == B and m.target(A) == B and m.target(B) == B
    for bad in ({"merges": [{"from": A, "to": A}]},
                {"merges": [{"from": A, "to": B}, {"from": B, "to": A}]},
                {"merges": [{"from": "nope", "to": B}]},
                {"merges": [{"from": A}]},
                {"merges": [{"from": A, "to": B}, {"from": A, "to": C}]}):
        with pytest.raises(ValueError):
            mg.parse_merges(bad)
    assert not mg.load_merges("does-not-exist.json")


def test_repo_merge_file_is_valid():
    m = mg.load_merges(Path(__file__).resolve().parent.parent / "player_merges.json")
    assert m.target(A) == B and m.by_name["lordhamster42"] == B
    assert m.target("c273fdfb-2181-47be-b174-6d8f20489b1b") == "76e49874-1786-4d4c-9527-4dfd741c989d"
    assert m.rename_target("NotQuiteRat_") == "bb8d717d-1e2f-4204-8ea5-88c90a60bca4"


def test_sum_timelines():
    a = [(10, 5), (30, 7)]
    b = [(20, 100), (30, 100), (40, 101)]
    assert sum_timelines([a, b]) == [(10, 5), (20, 105), (30, 107), (40, 108)]
    assert sum_timelines([[(10, 1)], []]) == [(10, 1)]


# ------------------------------------------------------------------ end to end

@pytest.fixture
def merged(world, run_snapshot):
    """A and B play separately on smp for two runs, then the merge is added."""
    (world.server_root("smp") / "usercache.json").write_text(json.dumps([
        {"name": "hampter", "uuid": B, "expiresOn": NEW}]), encoding="utf-8")
    world.set_scoreboard("smp", ["digs"], [("hampter", "digs", 10), ("LordHam", "digs", 3)],
                         {"t": ["hampter"]})
    world.add_stats("smp", A, stone(5))
    world.add_stats("smp", B, stone(100))
    run_snapshot(1000)
    world.add_stats("smp", A, stone(7))
    run_snapshot(2000)
    c = sqlite3.connect(world.db_path)
    c.execute("INSERT OR REPLACE INTO firsts (at, server_key, uuid, category, stat_key) "
              "VALUES (1000, 'smp', ?, 'minecraft:mined', 'minecraft:gold_ore')", (A,))
    c.commit()
    c.close()
    run_snapshot(3000, merges=merges_for("LordHam"))
    return world


def test_history_and_current_summed_under_target(merged, run_snapshot):
    w = merged
    assert q(w, "SELECT COUNT(*) FROM stat_snapshots WHERE uuid=?", A) == [(0,)]
    assert q(w, "SELECT at, value FROM stat_snapshots WHERE uuid=? AND stat_key='minecraft:stone' "
                "ORDER BY at", B) == [(1000, 105), (2000, 107)]
    assert q(w, "SELECT value FROM stat_current WHERE uuid=? AND stat_key='minecraft:stone'",
             B) == [(107,)]
    assert q(w, "SELECT COUNT(*) FROM stat_current WHERE uuid=?", A) == [(0,)]
    # scoreboard: the unresolved LordHam entry (name only) folded into B
    assert q(w, "SELECT participant_key, value FROM scoreboard_current WHERE objective='digs'") == [
        (B, 13)]
    assert q(w, "SELECT uuid, value FROM scoreboard_snapshots WHERE objective='digs' "
                "ORDER BY at") == [(B, 13)]
    assert q(w, "SELECT uuid FROM firsts WHERE stat_key='minecraft:gold_ore'") == [(B,)]
    assert q(w, "SELECT value FROM meta WHERE key=?", "merged:" + A) == [(B,)]
    # never active on either account: stays unknown (NULL), not 0 = 1970
    assert q(w, "SELECT last_activity FROM players WHERE uuid=?", B) == [(None,)]
    assert list(Path(w.db_path).parent.glob("pre-merge-*.db"))


def test_later_runs_sum_live_and_never_double_count(merged, run_snapshot):
    w = merged
    m = merges_for("LordHam")
    run_snapshot(4000, merges=m)  # nothing changed: no rows
    assert q(w, "SELECT COUNT(*) FROM stat_snapshots WHERE at=4000") == [(0,)]
    w.add_stats("smp", A, stone(9))
    w.set_scoreboard("smp", ["digs"], [("hampter", "digs", 10), ("LordHam", "digs", 4)],
                     {"t": ["hampter"]})
    run_snapshot(5000, merges=m)
    assert q(w, "SELECT value FROM stat_current WHERE uuid=? AND stat_key='minecraft:stone'",
             B) == [(109,)]
    assert q(w, "SELECT value FROM scoreboard_current WHERE participant_key=? AND "
                "objective='digs'", B) == [(14,)]
    assert q(w, "SELECT COUNT(*) FROM stat_current WHERE uuid=?", A) == [(0,)]


def test_api_resolves_merged_account(merged, monkeypatch, tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"merges": [{"from": A, "to": B, "names": ["LordHam"]}]}),
                 encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    mg.reset_api_merges()
    monkeypatch.setenv("CHRONOS_STATS_DB", merged.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        for ident in (A, "LordHam", B):
            j = c.get(f"/api/stats/player/{ident}").json()
            assert j["uuid"] == B, ident
    cache.clear()


def test_import_sums_merged_accounts(tmp_path, world, monkeypatch):
    import snapshot
    from conftest import make_backup
    from tools import import_backups as ib
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"merges": [{"from": A, "to": B, "names": ["LordHam"]}]}),
                 encoding="utf-8")
    monkeypatch.setattr(mg, "MERGES_PATH", p)
    conn = snapshot.connect_db(world.db_path)
    snapshot.sync_servers_table(conn, world.config)
    w = make_backup(tmp_path, name="b", last_played_ms=1_600_000_000_000,
                    stats={A: stone(5), B: stone(100)},
                    scoreboard=(["digs"], [("hampter", "digs", 10), ("LordHam", "digs", 3)]),
                    usercache={B: "hampter"})
    ib.import_backups(conn, "smp", [ib.read_backup(w, None)], {}, {}, False)
    assert conn.execute("SELECT uuid, value FROM stat_snapshots").fetchall() == [(B, 105)]
    assert conn.execute("SELECT uuid, value FROM scoreboard_snapshots").fetchall() == [(B, 13)]
    conn.close()
