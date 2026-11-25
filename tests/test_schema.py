import sqlite3

import snapshot
from sql.migrations import migrate

OLD_FIRSTS = """
CREATE TABLE firsts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    server_key  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    category    TEXT NOT NULL,
    stat_key    TEXT NOT NULL
);
"""


def cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_migrate_adds_earliest_known(tmp_path):
    conn = snapshot.connect_db(str(tmp_path / "a.db"))
    assert "earliest_known" in cols(conn, "firsts")
    assert "earliest_known" in cols(conn, "milestones")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name="
                        "'idx_milestones_feed'").fetchone()


def test_migrate_idempotent(tmp_path):
    conn = snapshot.connect_db(str(tmp_path / "a.db"))
    migrate(conn)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_migrate_existing_db(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript(OLD_FIRSTS)
    c.commit()
    c.close()
    conn = snapshot.connect_db(p)
    assert "earliest_known" in cols(conn, "firsts")
    assert "earliest_known" in cols(conn, "milestones")


def test_new_tables_exist(tmp_path):
    conn = snapshot.connect_db(str(tmp_path / "a.db"))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    for n in ("activity", "stat_totals", "stat_totals_current", "scoreboard_totals",
              "scoreboard_totals_current", "rivalries", "meta",
              "spotlight_candidates", "name_lookups", "idx_milestones_unique"):
        assert n in names


def test_fixture_snapshot_roundtrip(world, run_snapshot):
    u = "11111111-1111-1111-1111-111111111111"
    world.add_stats("smp", u, {"minecraft:mined": {"minecraft:stone": 5}})
    world.set_usercache("smp", {u: "Alice"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    run_snapshot(1000)
    conn = sqlite3.connect(world.db_path)
    row = conn.execute(
        "SELECT value FROM stat_current WHERE server_key='smp' AND uuid=? "
        "AND category='minecraft:mined' AND stat_key='minecraft:stone'", (u,)).fetchone()
    assert row == (5,)
