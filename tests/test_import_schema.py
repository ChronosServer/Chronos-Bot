import sqlite3

import pytest
from fastapi.testclient import TestClient

import snapshot
from api import cache
from api.main import create_app
from bot.derive_common import tracking_start
from sql.migrations import migrate

U1 = "11111111-1111-1111-1111-111111111111"


def cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_migrate_v3_columns_and_imports_table(tmp_path):
    conn = snapshot.connect_db(str(tmp_path / "a.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert "approximate" in cols(conn, "firsts")
    assert "approximate" in cols(conn, "milestones")
    assert cols(conn, "imports") == [
        "world_path", "level_name", "server_key", "last_played", "data_version",
        "imported_at", "rows_written"]


def test_migrate_v2_db_to_v3(tmp_path):
    conn = snapshot.connect_db(str(tmp_path / "a.db"))
    # roll back to a v2 shape
    conn.execute("ALTER TABLE firsts DROP COLUMN approximate")
    conn.execute("ALTER TABLE milestones DROP COLUMN approximate")
    conn.execute("DROP TABLE imports")
    conn.execute("PRAGMA user_version = 2")
    conn.execute("INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known)"
                 " VALUES (5, 'smp', ?, 'c', 'k', 0)", (U1,))
    conn.execute("INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key,"
                 " threshold, value_at, earliest_known) VALUES (5,'smp',?,'r','c','k',1,1,0)",
                 (U1,))
    conn.commit()
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert conn.execute("SELECT approximate FROM firsts").fetchall() == [(0,)]
    assert conn.execute("SELECT approximate FROM milestones").fetchall() == [(0,)]
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='imports'").fetchone()


def test_tracking_start_counts_import_runs(world, run_snapshot):
    world.add_stats("smp", U1, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1000)
    conn = sqlite3.connect(world.db_path)
    conn.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, error, entries_read,"
                 " rows_written, duration_ms) VALUES (100,'smp','import',1,NULL,5,5,0)")
    conn.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, error, entries_read,"
                 " rows_written, duration_ms) VALUES (50,'smp','import',1,NULL,0,0,0)")
    conn.commit()
    assert tracking_start(conn, "smp") == 100
    conn.close()
    run_snapshot(2000)
    conn = sqlite3.connect(world.db_path)
    assert conn.execute("SELECT value FROM meta WHERE key='tracking_started_at:smp'"
                        ).fetchone()[0] == "100"


def test_api_exposes_approximate(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice"})
    world.set_scoreboard("smp", ["kills"], [("Alice", "kills", 3)], {"t": ["Alice"]})
    world.add_stats("smp", U1, {"minecraft:mined": {"minecraft:stone": 5}})
    run_snapshot(1_000_000)
    c = sqlite3.connect(world.db_path)
    c.execute("DELETE FROM firsts")
    c.execute("DELETE FROM milestones")
    c.execute("INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known,"
              " approximate) VALUES (5,'smp',?,'c','k',0,1)", (U1,))
    c.execute("INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key,"
              " threshold, value_at, earliest_known, approximate) "
              "VALUES (5,'smp',?,'r','c','k',1,1,0,1)", (U1,))
    c.commit()
    c.close()
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as cl:
        assert cl.get("/api/stats/firsts").json()["rows"][0]["approximate"] == 1
        assert cl.get("/api/stats/milestones").json()["rows"][0]["approximate"] == 1
        p = cl.get("/api/stats/player/" + U1).json()
        assert p["firsts"][0]["approximate"] == 1
        assert p["milestones"][0]["approximate"] == 1
    cache.clear()
