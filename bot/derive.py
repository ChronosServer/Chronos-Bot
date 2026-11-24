"""Derive step: runs after the snapshot rows for a run are committed.

Everything here is derived from stat_snapshots / stat_current /
scoreboard_current; nothing reads world files. Participant rules (bots,
mirrors, unresolved) come from api.scope -- never reimplemented here.
Only provisioned=1 servers are iterated (the 'all' pseudo row is 0).
"""
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from derive_common import (  # noqa: E402,F401  (re-exported for callers/tests)
    ACTIVITY_CATEGORY, ACTIVITY_STAT, Scope, _servers, scoreboard_filter_sql,
    stat_filter_sql, tracking_start,
)
from derive_achievements import derive_badges, derive_firsts, derive_milestones  # noqa: E402
from derive_social import derive_rivalries, derive_spotlight  # noqa: E402


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
    # Full recompute every run: classification (bot flags) can change with no
    # stat change, and a previously failed derive must self-heal.
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
    for ck, total in totals.items():
        new = (total, nobots.get(ck, 0))
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
    scope = Scope(key, (key,))
    sql_all, p_all = scoreboard_filter_sql(scope, True, "sc", is_current=True)
    sql_nb, p_nb = scoreboard_filter_sql(scope, False, "sc", is_current=True)
    totals = dict(conn.execute(
        f"SELECT objective, SUM(value) FROM scoreboard_current sc WHERE {sql_all} "
        "GROUP BY objective", p_all).fetchall())
    nobots = dict(conn.execute(
        f"SELECT objective, SUM(value) FROM scoreboard_current sc WHERE {sql_nb} "
        "GROUP BY objective", p_nb).fetchall())
    cur = {o: (t, n) for o, t, n in conn.execute(
        "SELECT objective, total, total_no_bots FROM scoreboard_totals_current "
        "WHERE server_key=?", (key,))}
    for obj, total in totals.items():
        new = (total, nobots.get(obj, 0))
        if cur.get(obj) == new:
            continue
        conn.execute(
            "INSERT INTO scoreboard_totals (server_key, objective, at, total, total_no_bots) "
            "VALUES (?, ?, ?, ?, ?)", (key, obj, run_at, *new))
        conn.execute(
            "INSERT INTO scoreboard_totals_current (server_key, objective, total, total_no_bots) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(server_key, objective) DO UPDATE SET "
            "total=excluded.total, total_no_bots=excluded.total_no_bots",
            (key, obj, *new))


def derive_totals(conn, run_at):
    for key in _servers(conn):
        _derive_stat_totals(conn, key, run_at)
        _derive_scoreboard_totals(conn, key, run_at)
    conn.commit()


def resolve_names(conn, run_at, mojang):
    """Spend whatever is left of the run's shared Mojang budget resolving
    names of players we only know by uuid, most recently observed first."""
    if mojang is None:
        return
    pending = [r[0] for r in conn.execute(
        "SELECT uuid FROM players WHERE name_resolved = 0 "
        "ORDER BY last_observed DESC, uuid").fetchall()]
    for uuid in pending:
        if mojang.calls >= mojang.budget:
            break
        name = mojang.uuid_to_name(uuid)
        if name:
            # same effect as snapshot.upsert_player(..., name_resolved=True)
            # minus touching last_observed (a lookup is not an observation)
            conn.execute("UPDATE players SET name = ?, name_resolved = 1 WHERE uuid = ?",
                         (name, uuid))
    conn.commit()


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
    try:
        derive_activity(conn, run_at)
        derive_totals(conn, run_at)
        derive_firsts(conn, run_at)
        derive_milestones(conn, run_at)
        derive_rivalries(conn, run_at)
        derive_badges(conn, run_at)
        derive_spotlight(conn, run_at)
        resolve_names(conn, run_at, mojang)
    finally:
        conn.rollback()
        write_meta(conn, run_at)
