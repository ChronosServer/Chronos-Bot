"""Derive step: runs after the snapshot rows for a run are committed.

Everything here is derived from stat_snapshots / stat_current /
scoreboard_current; nothing reads world files. Participant rules (bots,
mirrors, unresolved) come from api.scope -- never reimplemented here.
Only provisioned=1 servers are iterated (the 'all' pseudo row is 0).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.scope import Scope, scoreboard_filter_sql, stat_filter_sql  # noqa: E402

ACTIVITY_CATEGORY = "minecraft:custom"
ACTIVITY_STAT = "minecraft:play_time"


def _servers(conn):
    return [r[0] for r in conn.execute(
        "SELECT key FROM servers WHERE provisioned=1 ORDER BY key")]


def tracking_start(conn, server_key):
    """MIN(at) of stats runs that actually read something."""
    return conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind='stats' "
        "AND ok=1 AND entries_read > 0", (server_key,)).fetchone()[0]


def derive_activity(conn, run_at):
    for key in _servers(conn):
        start = tracking_start(conn, key)
        # First qualifying run: every player is "changed", which is not activity.
        if start is None or start >= run_at:
            continue
        uuids = [r[0] for r in conn.execute(
            "SELECT DISTINCT uuid FROM stat_snapshots WHERE at=? AND server_key=? "
            "AND category=? AND stat_key=?",
            (run_at, key, ACTIVITY_CATEGORY, ACTIVITY_STAT))]
        conn.executemany(
            "INSERT OR IGNORE INTO activity (server_key, at, uuid) VALUES (?, ?, ?)",
            [(key, run_at, u) for u in uuids])
        conn.executemany(
            "UPDATE players SET last_activity="
            "(SELECT MAX(at) FROM activity WHERE uuid=?) WHERE uuid=?",
            [(u, u) for u in uuids])
    conn.commit()


def _derive_stat_totals(conn, key, run_at):
    touched = {(c, k) for c, k in conn.execute(
        "SELECT DISTINCT category, stat_key FROM stat_snapshots "
        "WHERE at=? AND server_key=?", (run_at, key))}
    if not touched:
        return
    sql, params = stat_filter_sql(Scope(key, (key,)), False, "s")
    totals = {(c, k): t for c, k, t in conn.execute(
        "SELECT category, stat_key, SUM(value) FROM stat_current "
        "WHERE server_key=? GROUP BY category, stat_key", (key,))}
    nobots = {(c, k): t for c, k, t in conn.execute(
        f"SELECT category, stat_key, SUM(value) FROM stat_current s WHERE {sql} "
        "GROUP BY category, stat_key", params)}
    cur = {(c, k): (t, n) for c, k, t, n in conn.execute(
        "SELECT category, stat_key, total, total_no_bots FROM stat_totals_current "
        "WHERE server_key=?", (key,))}
    for ck in touched:
        new = (totals.get(ck, 0), nobots.get(ck, 0))
        if cur.get(ck) == new:
            continue
        conn.execute(
            "INSERT INTO stat_totals (server_key, category, stat_key, at, total, total_no_bots) "
            "VALUES (?, ?, ?, ?, ?, ?)", (key, ck[0], ck[1], run_at, *new))
        conn.execute(
            "INSERT INTO stat_totals_current (server_key, category, stat_key, total, total_no_bots) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(server_key, category, stat_key) DO UPDATE SET "
            "total=excluded.total, total_no_bots=excluded.total_no_bots",
            (key, ck[0], ck[1], *new))


def _derive_scoreboard_totals(conn, key, run_at):
    touched = {r[0] for r in conn.execute(
        "SELECT DISTINCT objective FROM scoreboard_snapshots WHERE at=? AND server_key=?",
        (run_at, key))}
    if not touched:
        return
    sql, params = scoreboard_filter_sql(Scope(key, (key,)), True, "sc", is_current=True)
    totals = dict(conn.execute(
        f"SELECT objective, SUM(value) FROM scoreboard_current sc WHERE {sql} "
        "GROUP BY objective", params).fetchall())
    cur = dict(conn.execute(
        "SELECT objective, total FROM scoreboard_totals_current WHERE server_key=?", (key,)))
    for obj in touched:
        new = totals.get(obj, 0)
        if cur.get(obj) == new:
            continue
        conn.execute(
            "INSERT INTO scoreboard_totals (server_key, objective, at, total) VALUES (?, ?, ?, ?)",
            (key, obj, run_at, new))
        conn.execute(
            "INSERT INTO scoreboard_totals_current (server_key, objective, total) VALUES (?, ?, ?) "
            "ON CONFLICT(server_key, objective) DO UPDATE SET total=excluded.total",
            (key, obj, new))


def derive_totals(conn, run_at):
    for key in _servers(conn):
        _derive_stat_totals(conn, key, run_at)
        _derive_scoreboard_totals(conn, key, run_at)
    conn.commit()


# Stubs for later tasks; orchestration order is final.
def derive_firsts(conn, run_at):
    pass


def derive_milestones(conn, run_at):
    pass


def derive_rivalries(conn, run_at):
    pass


def derive_badges(conn, run_at):
    pass


def derive_spotlight(conn, run_at):
    pass


def resolve_names(conn, run_at, mojang):
    pass


def write_meta(conn, run_at):
    meta = {
        "data_version": str(conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM snapshot_runs").fetchone()[0]),
        "last_run_at": str(run_at),
    }
    for key in _servers(conn):
        start = tracking_start(conn, key)
        if start is not None:
            meta[f"tracking_started_at:{key}"] = str(start)
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", list(meta.items()))
    conn.commit()


def run_derive(conn, run_at, mojang=None):
    derive_activity(conn, run_at)
    derive_totals(conn, run_at)
    derive_firsts(conn, run_at)
    derive_milestones(conn, run_at)
    derive_rivalries(conn, run_at)
    derive_badges(conn, run_at)
    derive_spotlight(conn, run_at)
    resolve_names(conn, run_at, mojang)
    write_meta(conn, run_at)
