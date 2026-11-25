"""The first live run is a full observation: write the 0 rows it implies.

A server's first live snapshot L (per table) wrote a row for EVERY key a
player had then (current tables were empty). Backups are imported later,
as change-only rows before L. A key a backup has but the live files don't
(a stat reset, a removed objective, a reset score) therefore has no row at
L, and "latest row <= T" kept returning the stale backup value for every
T >= L, while the current tables have nothing for it (= 0). That made
delta leaderboards negative (0 - backup value) and /series flat at the old
value. Writing (L, 0) for exactly those keys makes every history read agree
with the live data; the totals backfill already counts them as 0 from L on.

Idempotent: keys that already have a row at L, or whose last value before L
is 0, get nothing. Run once on an existing DB (snapshot, meta
`live_start_filled`) and after every backup import (they add rows before L).
No commit.
"""
META = "live_start_filled"


def live_start(conn, server_key, kind):
    return conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind=? AND ok=1 "
        "AND entries_read > 0", (server_key, kind)).fetchone()[0]


def fill_server(conn, server_key):
    """Returns (stat rows, scoreboard rows) written."""
    n_stat = n_board = 0
    L = live_start(conn, server_key, "stats")
    if L is not None:
        n_stat = conn.execute(
            "INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
            "SELECT ?, p.server_key, p.uuid, p.category, p.stat_key, 0 FROM ("
            "  SELECT server_key, uuid, category, stat_key, value, MAX(at) AS at "
            "  FROM stat_snapshots INDEXED BY idx_stat_point WHERE server_key=? AND at<? "
            "  GROUP BY category, stat_key, uuid) p "
            "WHERE p.value != 0 AND NOT EXISTS (SELECT 1 FROM stat_snapshots t "
            "  INDEXED BY idx_stat_point WHERE t.server_key=p.server_key "
            "  AND t.category=p.category AND t.stat_key=p.stat_key AND t.uuid=p.uuid AND t.at=?)",
            (L, server_key, L, L)).rowcount
    L = live_start(conn, server_key, "scoreboards")
    if L is not None:
        n_board = conn.execute(
            "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
            "objective, value) "
            "SELECT ?, p.server_key, p.participant_kind, p.uuid, p.raw_name, p.objective, 0 FROM ("
            "  SELECT server_key, participant_kind, uuid, raw_name, objective, value, MAX(at) AS at "
            "  FROM scoreboard_snapshots INDEXED BY idx_scoreboard_point "
            "  WHERE server_key=? AND at<? "
            "  GROUP BY objective, uuid, CASE WHEN uuid IS NULL THEN raw_name END) p "
            "WHERE p.value != 0 AND NOT EXISTS (SELECT 1 FROM scoreboard_snapshots t "
            "  WHERE t.server_key=p.server_key AND t.objective=p.objective AND t.at=? "
            "  AND (t.uuid=p.uuid OR (t.uuid IS NULL AND p.uuid IS NULL "
            "       AND t.raw_name=p.raw_name)))",
            (L, server_key, L, L)).rowcount
    return n_stat, n_board


def fill_all(conn):
    out = {}
    for (key,) in conn.execute("SELECT key FROM servers WHERE provisioned=1").fetchall():
        out[key] = fill_server(conn, key)
    conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') ON CONFLICT(key) "
                 "DO UPDATE SET value='1'", (META,))
    return out


def ensure_filled(conn):
    """Once per DB (meta flag); later imports call fill_server themselves."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (META,)).fetchone()
    if row and row[0] == "1":
        return None
    out = fill_all(conn)
    conn.commit()
    return out
