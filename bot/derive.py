"""Derive step: runs after the snapshot rows for a run are committed.

Everything here is derived from stat_snapshots / stat_current /
scoreboard_current; nothing reads world files. Participant rules (bots,
mirrors, unresolved) come from api.scope -- never reimplemented here.
Only provisioned=1 servers are iterated (the 'all' pseudo row is 0).
"""
import hashlib
import json
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from derive_common import (  # noqa: E402,F401  (re-exported for callers/tests)
    DEFAULTS, Scope, _servers, counted_uuids, is_fake_uuid, history_points,
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
    sql, params = stat_filter_sql(Scope(key, (key,)), False, "s", track=None)
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
    sql_all, p_all = scoreboard_filter_sql(scope, True, "sc", is_current=True, track=None)
    sql_nb, p_nb = scoreboard_filter_sql(scope, False, "sc", is_current=True, track=None)
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
COUNTED_SET = "counted_set:"  # meta key prefix + server key: JSON sorted uuid list
COUNTED_FP = "counted_fp:"    # legacy (sha1 only, no diff possible); read once, then dropped
_FLUSH = 10_000
_CHUNK = 500  # uuids per IN (...) list


def counted_sets(conn):
    """{server: set of counted uuids} for bot-filtering servers (one query
    each; servers that never filter bots have none)."""
    out = {}
    for key in _servers(conn):
        counted = counted_uuids(conn, key)
        if counted is not None:
            out[key] = set(counted)
    return out


def _fingerprint(uuids):
    return hashlib.sha1(",".join(sorted(uuids)).encode()).hexdigest()


def _store_counted(conn, sets):
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(COUNTED_SET + k, json.dumps(sorted(v))) for k, v in sets.items()])
    conn.execute("DELETE FROM meta WHERE key LIKE ?", (COUNTED_FP + "%",))


def _has_history_before(conn, key, uuids, run_at):
    """Any stat/scoreboard history row on this server for these uuids before
    run_at (seeks the per-player indexes)."""
    uuids = sorted(uuids)
    for i in range(0, len(uuids), _CHUNK):
        part = uuids[i:i + _CHUNK]
        marks = ",".join("?" * len(part))
        for sql in (f"SELECT 1 FROM stat_snapshots INDEXED BY idx_stat_player "
                    f"WHERE uuid IN ({marks}) AND server_key=? AND at<? LIMIT 1",
                    f"SELECT 1 FROM scoreboard_snapshots INDEXED BY idx_scoreboard_player "
                    f"WHERE uuid IN ({marks}) AND uuid IS NOT NULL AND server_key=? "
                    f"AND at<? LIMIT 1"):
            if conn.execute(sql, [*part, key, run_at]).fetchone():
                return True
    return False


def check_counted(conn, run_at):
    """total_no_bots history uses TODAY's bot flags for all history, so a
    reclassification (a player's flag flips with no stat change) must
    rebuild it, or the series steps at the flip run. Per bot-filtering
    server, the counted uuid set is compared with the stored one; the
    server's totals history needs a rebuild only when a uuid that flipped
    (newly counted or uncounted) has history before run_at -- a brand-new
    team member with no earlier rows changes nothing before run_at.
    Legacy DB (sha1 fingerprint only): a differing fingerprint rebuilds that
    server (the diff is unknown). Nothing stored (first run): just store.
    Returns the sorted servers to rebuild; stores the current sets."""
    sets = counted_sets(conn)
    stored = dict(conn.execute("SELECT key, value FROM meta WHERE key LIKE ? OR key LIKE ?",
                               (COUNTED_SET + "%", COUNTED_FP + "%")).fetchall())
    rebuild = []
    for k, cur in sets.items():
        raw = stored.get(COUNTED_SET + k)
        if raw is not None:
            try:
                old = set(json.loads(raw))
            except (ValueError, TypeError):
                old = None
            if old is None:
                rebuild.append(k)
            elif old != cur and _has_history_before(conn, k, old ^ cur, run_at):
                rebuild.append(k)
        elif stored.get(COUNTED_FP + k) not in (None, _fingerprint(cur)):
            rebuild.append(k)
    _store_counted(conn, sets)
    return sorted(rebuild)


def _backfill_server(conn, key, run_at, emit_stat, emit_board):
    counted = counted_uuids(conn, key)  # current rule, once per server

    def is_counted(u):
        return not is_fake_uuid(u) and (counted is None or u in counted)

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


def _backfill(conn, run_at):
    """One-time rebuild of stat_totals / scoreboard_totals history from the
    raw snapshot history (which predates the first derive on the live DB),
    so totals series have no 0 -> total cliff on deploy day. Bot status is
    the CURRENT rule for every historical row (history of flags isn't
    stored); check_counted rebuilds a server when its counted set changes
    for a uuid with earlier history. Emits only rows with at < run_at; the
    normal recompute then writes this run's changes. Guarded by
    meta[BACKFILL_MARKER]; no commit (derive_totals commits it together with
    the totals, rebuild_from_points inside its own transaction). True when
    it rebuilt anything."""
    if conn.execute("SELECT 1 FROM meta WHERE key=? AND value='1'",
                    (BACKFILL_MARKER,)).fetchone():
        return False
    for tbl in ("stat_totals", "stat_totals_current",
                "scoreboard_totals", "scoreboard_totals_current"):
        conn.execute(f"DELETE FROM {tbl}")  # incl. servers no longer provisioned
    rebuild_totals(conn, run_at, _servers(conn))
    conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (BACKFILL_MARKER,))
    _store_counted(conn, counted_sets(conn))  # the classification used
    return True


def rebuild_totals(conn, run_at, servers):
    """Replace these servers' totals history (rows at < run_at) and their
    *_totals_current with a replay of their raw history. No commit."""
    for key in servers:
        for tbl in ("stat_totals", "stat_totals_current",
                    "scoreboard_totals", "scoreboard_totals_current"):
            conn.execute(f"DELETE FROM {tbl} WHERE server_key=?", (key,))
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
    # Stored together with the totals (one commit). A full backfill (marker
    # missing) covers every server, so the per-server rebuild is skipped then.
    changed = check_counted(conn, run_at)
    if not _backfill(conn, run_at) and changed:
        rebuild_totals(conn, run_at, changed)
    for key in _servers(conn):
        _derive_stat_totals(conn, key, run_at)
        _derive_scoreboard_totals(conn, key, run_at)
    conn.commit()


def resolve_names(conn, run_at, mojang, live_named=None):
    """Spend whatever is left of the run's shared Mojang budget: first on
    names of players we only know by uuid, then (when live_named is given)
    on refreshing the names of real players (a stat file somewhere) that no
    live usercache names this run -- their stored name may be an old one
    from a backup or an outdated usercache, and a rename is otherwise never
    seen. Most recently observed first. Mojang results (failures too) are
    cached in name_lookups for mojang.retry_after_days, so each uuid costs
    at most one call per period and cached answers are applied for free.
    live_named: uuids whose name came from a live usercache this run."""
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
    if live_named is not None:
        real = {r[0] for r in conn.execute("SELECT DISTINCT uuid FROM stat_current")}
        for uuid, old in conn.execute(
                "SELECT uuid, name FROM players WHERE name_resolved = 1 "
                "ORDER BY last_observed DESC, uuid").fetchall():
            if uuid in live_named or uuid not in real:
                continue
            name = mojang.uuid_to_name(uuid)  # cached answers work past the budget
            if name and name != old:
                conn.execute("UPDATE players SET name = ? WHERE uuid = ?", (name, uuid))
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
        # api.history: where history is sparse (imports) vs dense (live)
        points, live = history_points(conn, key)
        if points:
            meta[f"history_points:{key}"] = json.dumps(points)
        if live is not None:
            meta[f"live_started_at:{key}"] = str(live)
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", list(meta.items()))


def run_derive(conn, run_at, mojang=None, settings=None, live_named=None):
    """settings: api.settings.Settings built by snapshot.py from config.json
    (DEFAULTS if None). live_named: see resolve_names."""
    settings = settings or DEFAULTS
    try:
        derive_activity(conn, run_at, settings)
        derive_totals(conn, run_at)
        derive_firsts(conn, run_at)
        derive_milestones(conn, run_at, settings)
        derive_rivalries(conn, run_at, settings)
        derive_badges(conn, run_at, settings)
        derive_spotlight(conn, run_at, settings)
        resolve_names(conn, run_at, mojang, live_named)
    finally:
        conn.rollback()
        write_meta(conn, run_at)
