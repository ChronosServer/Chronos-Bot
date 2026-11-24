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
    DEFAULTS, Scope, _servers, counted_uuids,
    scoreboard_filter_sql, stat_filter_sql, tracking_start,
)
from derive_achievements import derive_badges, derive_firsts, derive_milestones  # noqa: E402
from derive_social import derive_rivalries, derive_spotlight  # noqa: E402


def derive_activity(conn, run_at, settings=None):
    category, stat = (settings or DEFAULTS).activity_stat
    for key in _servers(conn):
        start = tracking_start(conn, key)
        # First qualifying run: every player is "changed", which is not activity.
        if start is None or start >= run_at:
            continue
        uuids = [r[0] for r in conn.execute(
            # pinned to idx_stat_at (server_key, at): a seek to this run's
            # rows, not every activity-stat row ever written (schema.sql creates it)
            "SELECT DISTINCT uuid FROM stat_snapshots INDEXED BY idx_stat_at "
            "WHERE server_key=? AND at=? AND category=? AND stat_key=?",
            (key, run_at, category, stat))]
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


BACKFILL_MARKER = "totals_backfilled"
_FLUSH = 10_000


def _backfill_server(conn, key, run_at, emit_stat, emit_board):
    counted = counted_uuids(conn, key)  # current rule, once per server

    def is_counted(u):
        return counted is None or u in counted

    def first_live(kind):
        return conn.execute(
            "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind=? AND ok=1 "
            "AND entries_read>0", (key, kind)).fetchone()[0]

    def replay(cursor, emit, cutoff=None, alive=()):
        """cutoff: the first live run, a full observation. From there on a
        (participant, key) with history but absent from the current tables
        (alive) -- e.g. a player or score only in an imported backup --
        counts 0, so totals at cutoff equal the live current-based
        recompute. Before any import, history-latest == current: no-op."""
        last_val: dict = {}
        tot: dict = {}
        emitted: dict = {}
        touched: set = set()
        cur_at = None
        if cutoff is None or cutoff >= run_at:
            cutoff = None

        def apply(u, k, v):
            prev = last_val.get((u, k), 0)
            last_val[(u, k)] = v
            t = tot.get(k)
            if t is None:
                t = tot[k] = [0, 0]
            t[0] += v - prev
            if is_counted(u):
                t[1] += v - prev
            touched.add(k)

        def cut():
            for (u, k), v in list(last_val.items()):
                if v and (u, k) not in alive:
                    apply(u, k, 0)

        def flush(at):
            for k in touched:
                pair = tuple(tot[k])
                if emitted.get(k) != pair:
                    emitted[k] = pair
                    emit(k, at, *pair)
            touched.clear()

        for at, u, k, v in cursor:
            if cutoff is not None and at >= cutoff:
                if cur_at is not None:
                    flush(cur_at)
                cur_at, cutoff = cutoff, None
                cut()
            if at != cur_at:
                if cur_at is not None:
                    flush(cur_at)
                cur_at = at
            apply(u, k, v)
        if cutoff is not None:  # no history row at/after the first live run
            if cur_at is not None:
                flush(cur_at)
            cur_at = cutoff
            cut()
        if cur_at is not None:
            flush(cur_at)
        return emitted

    stats = replay(
        ((at, u, (c, s), v) for at, u, c, s, v in conn.execute(
            "SELECT at, uuid, category, stat_key, value FROM stat_snapshots "
            "INDEXED BY idx_stat_at WHERE server_key=? AND at<? ORDER BY at",
            (key, run_at))),
        emit_stat, first_live("stats"),
        {(u, (c, s)) for u, c, s in conn.execute(
            "SELECT uuid, category, stat_key FROM stat_current WHERE server_key=?", (key,))})
    mirrors = {o for (o,) in conn.execute(
        "SELECT objective FROM scoreboard_meta WHERE server_key=? AND is_vanilla_mirror=1",
        (key,))}
    boards = replay(
        ((at, u, o, v) for at, u, o, v in conn.execute(
            "SELECT at, uuid, objective, value FROM scoreboard_snapshots "
            "INDEXED BY idx_scoreboard_at WHERE server_key=? AND at<? "
            "AND participant_kind != 'unresolved' AND uuid IS NOT NULL ORDER BY at",
            (key, run_at)) if o not in mirrors),
        emit_board, first_live("scoreboards"),
        set(conn.execute(
            "SELECT participant_key, objective FROM scoreboard_current WHERE server_key=? "
            "AND participant_key NOT LIKE 'unresolved:%'", (key,))))
    return stats, boards


def backfill_totals(conn, run_at):
    """One-time rebuild of stat_totals / scoreboard_totals history from the
    raw snapshot history (which predates the first derive on the live DB),
    so totals series have no 0 -> total cliff on deploy day. Bot status is
    the CURRENT rule for every historical row (history of flags isn't
    stored). Emits only rows with at < run_at; the normal recompute then
    writes this run's changes. Guarded by meta[BACKFILL_MARKER], committed
    together with the rebuilt rows."""
    if _backfill(conn, run_at):
        conn.commit()


def _backfill(conn, run_at):
    """backfill_totals without the commit (rebuild_from_points runs it inside
    its own transaction). True when it rebuilt anything."""
    if conn.execute("SELECT 1 FROM meta WHERE key=? AND value='1'",
                    (BACKFILL_MARKER,)).fetchone():
        return False
    for tbl in ("stat_totals", "stat_totals_current",
                "scoreboard_totals", "scoreboard_totals_current"):
        conn.execute(f"DELETE FROM {tbl}")
    for key in _servers(conn):
        srows, brows = [], []

        def emit_stat(k, at, t, n):
            srows.append((key, k[0], k[1], at, t, n))
            if len(srows) >= _FLUSH:
                _flush_stat(conn, srows)

        def emit_board(o, at, t, n):
            brows.append((key, o, at, t, n))
            if len(brows) >= _FLUSH:
                _flush_board(conn, brows)

        stats, boards = _backfill_server(conn, key, run_at, emit_stat, emit_board)
        _flush_stat(conn, srows)
        _flush_board(conn, brows)
        conn.executemany(
            "INSERT INTO stat_totals_current (server_key, category, stat_key, total, "
            "total_no_bots) VALUES (?, ?, ?, ?, ?)",
            [(key, k[0], k[1], t, n) for k, (t, n) in stats.items()])
        conn.executemany(
            "INSERT INTO scoreboard_totals_current (server_key, objective, total, "
            "total_no_bots) VALUES (?, ?, ?, ?)",
            [(key, o, t, n) for o, (t, n) in boards.items()])
    conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (BACKFILL_MARKER,))
    return True


def _flush_stat(conn, rows):
    conn.executemany(
        "INSERT INTO stat_totals (server_key, category, stat_key, at, total, total_no_bots) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    rows.clear()


def _flush_board(conn, rows):
    conn.executemany(
        "INSERT INTO scoreboard_totals (server_key, objective, at, total, total_no_bots) "
        "VALUES (?, ?, ?, ?, ?)", rows)
    rows.clear()


def derive_totals(conn, run_at):
    backfill_totals(conn, run_at)
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
        if mojang.exhausted:
            break
        name = mojang.uuid_to_name(uuid)
        if name:
            # same effect as snapshot.upsert_player(..., name_resolved=True)
            # minus touching last_observed (a lookup is not an observation)
            conn.execute("UPDATE players SET name = ?, name_resolved = 1 WHERE uuid = ?",
                         (name, uuid))
    conn.commit()


def write_meta(conn, run_at):
    _write_meta(conn, run_at)
    conn.commit()


def _write_meta(conn, run_at):
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


def run_derive(conn, run_at, mojang=None, settings=None):
    """settings: api.settings.Settings built by snapshot.py from config.json
    (DEFAULTS if None)."""
    settings = settings or DEFAULTS
    try:
        derive_activity(conn, run_at, settings)
        derive_totals(conn, run_at)
        derive_firsts(conn, run_at)
        derive_milestones(conn, run_at, settings)
        derive_rivalries(conn, run_at, settings)
        derive_badges(conn, run_at, settings)
        derive_spotlight(conn, run_at, settings)
        resolve_names(conn, run_at, mojang)
    finally:
        conn.rollback()
        write_meta(conn, run_at)
