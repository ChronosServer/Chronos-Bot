"""Helpers shared by the derive stages (derive.py, derive_achievements.py,
derive_social.py). Participant rules come from api.scope, never reimplemented.
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.scope import (  # noqa: E402,F401
    Scope, counted_uuids, scoreboard_filter_sql, stat_filter_sql,
)
from api.settings import DAY, DEFAULTS, days_s  # noqa: E402,F401
from api.history import snap_from  # noqa: E402

# Tuning values come from api.settings (config.json stats_tracking); these
# are fixed facts, not tuning.
SCOREBOARD_CATEGORY = "scoreboard"
WEEK = 7 * DAY  # the rivalry gap_7d_ago window (a column name / API field)
TICKS_PER_HOUR = 20 * 60 * 60  # Minecraft: 20 ticks per second
# Backup import spec (Global Constraints), not tuning: how long a by-hand
# tool waits for the write lock before deciding a live snapshot is running.
BUSY_TIMEOUT_S = 5
BUSY_MESSAGE = ("a snapshot is running — stop chronos-stats-snapshot.timer "
                "and .service first")


class DatabaseBusy(RuntimeError):
    pass


def begin_immediate(conn, timeout_s=BUSY_TIMEOUT_S):
    """BEGIN IMMEDIATE (the write lock) on conn, waiting up to timeout_s;
    DatabaseBusy when another writer (a live snapshot) holds it."""
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_s * 1000)}")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            raise DatabaseBusy(BUSY_MESSAGE) from e
        raise


def _servers(conn):
    return [r[0] for r in conn.execute(
        "SELECT key FROM servers WHERE provisioned=1 ORDER BY key")]


def tracking_start(conn, server_key):
    """MIN(at) of stats/import runs that actually read something."""
    return conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind IN ('stats','import') "
        "AND ok=1 AND entries_read > 0", (server_key,)).fetchone()[0]


def history_points(conn, server_key):
    """(points, live): the server's import points plus its first live stats
    run L, sorted; live = L (None before any live run). Same qualifying
    runs as tracking_start (ok=1, entries_read > 0)."""
    live = conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind='stats' "
        "AND ok=1 AND entries_read > 0", (server_key,)).fetchone()[0]
    points = {r[0] for r in conn.execute(
        "SELECT DISTINCT at FROM snapshot_runs WHERE server_key=? AND kind='import' "
        "AND ok=1 AND entries_read > 0", (server_key,))}
    if live is not None:
        points.add(live)
    return sorted(points), live


def counted_stats(key, alias):
    """WHERE fragment: counted players' rows on one server (any table with
    server_key + uuid columns)."""
    return stat_filter_sql(Scope(key, (key,)), False, alias)


def counted_all(conn, alias):
    """Same, across every provisioned server."""
    return stat_filter_sql(Scope("all", tuple(_servers(conn))), False, alias)


def counted_scoreboard(key, alias, is_current):
    return scoreboard_filter_sql(Scope(key, (key,)), False, alias, is_current=is_current)


def clamped_ago(conn, key, run_at, seconds):
    """run_at - seconds, clamped to tracking start (None if never tracked),
    then snapped forward to the next history point when it falls in the
    sparse span before live tracking (api.history.snap_from)."""
    start = tracking_start(conn, key)
    if start is None:
        return None
    return snap_from(max(run_at - seconds, start), *history_points(conn, key))


def stat_value_at_sql(alias, uuid_col="uuid"):
    """Correlated scalar: value of alias's (server, category, key) for
    alias.<uuid_col> at time ? (latest row at <= T; 0 if none)."""
    return (
        "COALESCE((SELECT h.value FROM stat_snapshots h WHERE h.server_key={a}.server_key "
        "AND h.category={a}.category AND h.stat_key={a}.stat_key AND h.uuid={a}.{u} "
        "AND h.at <= ? ORDER BY h.at DESC LIMIT 1), 0)").format(a=alias, u=uuid_col)
