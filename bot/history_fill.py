"""History must agree with the live data: write the 0 rows it implies.

Backups are imported as change-only rows before a server's first live run L.
A key a backup has but the live files don't (a stat reset, a removed
objective, a reset score) is absent from stat_current / scoreboard_current,
yet "latest row <= T" kept returning the stale backup value for every
T >= L. That made delta leaderboards negative (0 - backup value) and
/series flat at the old value. Writing (L, 0) for exactly those keys makes
every history read agree with the live data (the totals backfill already
counts them as 0 from L on).

A key gets a 0 row only if ALL hold: it is absent from the current table,
its latest history row is non-zero, and that row is before L. A key present
in the current table is never touched: it was observed live, and its
unchanged value may well live in a row older than L (on the live DB some
servers got full rows from runs before the first counted one).

Idempotent. Run once on an existing DB (snapshot, meta `live_start_filled`)
and after every backup import (they add rows before L). No commit.
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
            "  FROM stat_snapshots INDEXED BY idx_stat_point WHERE server_key=? "
            "  GROUP BY category, stat_key, uuid) p "
            "WHERE p.value != 0 AND p.at < ? AND NOT EXISTS (SELECT 1 FROM stat_current c "
            "  WHERE c.server_key=p.server_key AND c.uuid=p.uuid "
            "  AND c.category=p.category AND c.stat_key=p.stat_key)",
            (L, server_key, L)).rowcount
    L = live_start(conn, server_key, "scoreboards")
    if L is not None:
        n_board = conn.execute(
            "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
            "objective, value) "
            "SELECT ?, p.server_key, p.participant_kind, p.uuid, p.raw_name, p.objective, 0 FROM ("
            "  SELECT server_key, participant_kind, uuid, raw_name, objective, value, MAX(at) AS at "
            "  FROM scoreboard_snapshots INDEXED BY idx_scoreboard_point WHERE server_key=? "
            "  GROUP BY objective, uuid, CASE WHEN uuid IS NULL THEN raw_name END) p "
            "WHERE p.value != 0 AND p.at < ? AND NOT EXISTS (SELECT 1 FROM scoreboard_current c "
            "  WHERE c.server_key=p.server_key AND c.objective=p.objective "
            "  AND c.participant_key = COALESCE(p.uuid, 'unresolved:' || p.raw_name))",
            (L, server_key, L)).rowcount
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


SPLIT_META = "first_run_split_collapsed"
SPLIT_WINDOW_S = 60


def collapse_split_first_run(conn, server_key, L=None):
    """The live DB's first scoreboard run wrote its rows a few seconds before
    the time its snapshot_runs row says (F = 13:50:55, L = 13:50:58 on smp,
    from older code). The fill's 0 rows sit at L, so a key with rows at both
    F and L is one a fold summed with a name/alt that was already gone: at F
    the sum still held that name's stale backup value (a spike), at L the
    right one. Both rows describe the same run: give F the L value and drop
    the L row. Idempotent; returns the keys fixed. No commit."""
    if L is None:
        L = live_start(conn, server_key, "scoreboards")
    if L is None:
        return 0
    pairs = conn.execute(
        "SELECT l.id, l.value, l.objective, l.uuid, l.raw_name FROM scoreboard_snapshots l "
        "WHERE l.server_key=? AND l.at=? AND EXISTS (SELECT 1 FROM scoreboard_snapshots f "
        "  WHERE f.server_key=l.server_key AND f.objective=l.objective AND f.uuid IS l.uuid "
        "  AND (l.uuid IS NOT NULL OR f.raw_name=l.raw_name) AND f.at>=? AND f.at<?)",
        (server_key, L, L - SPLIT_WINDOW_S, L)).fetchall()
    for lid, value, obj, uuid, raw in pairs:
        conn.execute(
            "UPDATE scoreboard_snapshots SET value=? WHERE server_key=? AND objective=? "
            "AND uuid IS ? AND (? IS NOT NULL OR raw_name=?) AND at>=? AND at<?",
            (value, server_key, obj, uuid, uuid, raw, L - SPLIT_WINDOW_S, L))
        conn.execute("DELETE FROM scoreboard_snapshots WHERE id=?", (lid,))
    return len(pairs)
