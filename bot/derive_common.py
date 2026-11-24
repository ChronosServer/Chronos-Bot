"""Helpers shared by the derive stages (derive.py, derive_achievements.py,
derive_social.py). Participant rules come from api.scope, never reimplemented.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.scope import (  # noqa: E402,F401
    Scope, counted_uuids, scoreboard_filter_sql, stat_filter_sql,
)

ACTIVITY_CATEGORY = "minecraft:custom"
ACTIVITY_STAT = "minecraft:play_time"
SCOREBOARD_CATEGORY = "scoreboard"
DAY = 86400
WEEK = 7 * DAY


def _servers(conn):
    return [r[0] for r in conn.execute(
        "SELECT key FROM servers WHERE provisioned=1 ORDER BY key")]


def tracking_start(conn, server_key):
    """MIN(at) of stats runs that actually read something."""
    return conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind='stats' "
        "AND ok=1 AND entries_read > 0", (server_key,)).fetchone()[0]


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
    """run_at - seconds, clamped to tracking start (None if never tracked)."""
    start = tracking_start(conn, key)
    if start is None:
        return None
    return max(run_at - seconds, start)


def stat_value_at_sql(alias, uuid_col="uuid"):
    """Correlated scalar: value of alias's (server, category, key) for
    alias.<uuid_col> at time ? (latest row at <= T; 0 if none)."""
    return (
        "COALESCE((SELECT h.value FROM stat_snapshots h WHERE h.server_key={a}.server_key "
        "AND h.category={a}.category AND h.stat_key={a}.stat_key AND h.uuid={a}.{u} "
        "AND h.at <= ? ORDER BY h.at DESC LIMIT 1), 0)").format(a=alias, u=uuid_col)
