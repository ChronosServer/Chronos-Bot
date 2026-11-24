"""Derive stages: firsts, milestones, badges (spec "Derived data" + Addendum A1).

Seeding: on the first derive where meta `seeded_achievements` is absent,
everything that already exists is recorded with earliest_known=1 ("already
had", no fake date). A server's own first qualifying run is treated the same
way (every row is "changed" then, which is not an observed achievement).
All writes are INSERT OR IGNORE against unique indexes, so re-running a
derive for the same run_at never duplicates anything.

Firsts and milestones are computed from row sources (SQL yielding counted
players' (server_key, uuid, category, stat_key, value[, prev])): the live
stages use current values or this run's rows; rebuild_from_points uses the
value at each imported backup point. The INSERT logic is shared.
"""
import json
import time

from derive_common import (  # noqa: F401  (DatabaseBusy/BUSY_MESSAGE re-exported)
    BUSY_MESSAGE, BUSY_TIMEOUT_S, DEFAULTS, SCOREBOARD_CATEGORY, TICKS_PER_HOUR,
    DatabaseBusy, _servers, begin_immediate, counted_scoreboard, counted_stats, days_s,
    stat_value_at_sql, tracking_start,
)

SEED_MARKER = "seeded_achievements"

# Milestone rules and badge thresholds: settings.milestones / settings.badges
# (config.json stats_tracking; defaults in api/settings.py).


# ----------------------------------------------------------------- seeding mode

def _seeded(conn):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (SEED_MARKER,)).fetchone()
    return bool(row) and row[0] == "1"


def _seed_modes(conn, run_at):
    """server_key -> True when this run must seed (earliest_known=1)."""
    seeded = _seeded(conn)
    out = {}
    for key in _servers(conn):
        start = tracking_start(conn, key)
        out[key] = (not seeded) or start is None or start >= run_at
    return out


def _sources(key, run_at):
    """'run' sources are pinned (INDEXED BY) to the (server_key, at) indexes so
    reading this run's rows is a seek, not a scan of all history.

    Normalized (server_key, uuid, category, stat_key, value) row sources of
    counted players with value > 0: 'current' = every current value, 'run' =
    rows written at run_at. Scoreboards use category 'scoreboard'."""
    fs, ps = counted_stats(key, "s")
    fc, pc = counted_scoreboard(key, "sc", True)
    fr, pr = counted_scoreboard(key, "ss", False)
    sb_prev = (
        "COALESCE((SELECT h.value FROM scoreboard_snapshots h WHERE h.server_key=x.server_key "
        "AND h.objective=x.stat_key AND h.uuid=x.uuid AND h.at <= ? "
        "ORDER BY h.at DESC LIMIT 1), 0)")
    return {
        "current": [
            ("SELECT s.server_key, s.uuid, s.category, s.stat_key, s.value FROM stat_current s "
             f"WHERE {fs} AND s.value > 0", ps, None),
            (f"SELECT sc.server_key, sc.participant_key AS uuid, '{SCOREBOARD_CATEGORY}' AS category, "
             "sc.objective AS stat_key, sc.value FROM scoreboard_current sc "
             f"WHERE {fc} AND sc.value > 0", pc, None),
        ],
        "run": [
            ("SELECT s.server_key, s.uuid, s.category, s.stat_key, s.value "
             "FROM stat_snapshots s INDEXED BY idx_stat_at "
             f"WHERE s.server_key = ? AND s.at = ? AND {fs} AND s.value > 0", [key, run_at, *ps],
             stat_value_at_sql("x")),
            (f"SELECT ss.server_key, ss.uuid, '{SCOREBOARD_CATEGORY}' AS category, "
             "ss.objective AS stat_key, ss.value "
             "FROM scoreboard_snapshots ss INDEXED BY idx_scoreboard_at "
             f"WHERE ss.server_key = ? AND ss.at = ? AND {fr} AND ss.value > 0",
             [key, run_at, *pr], sb_prev),
        ],
    }


# Value at T (latest row at <= T) of every (key, uuid) of one server, in one
# ordered pass of the point index -- set-based, never a per-row lookup.
_STAT_AT = (
    "SELECT server_key, uuid, category, stat_key, value, MAX(at) AS at "
    "FROM stat_snapshots INDEXED BY idx_stat_point WHERE server_key = ? AND at <= ? "
    "GROUP BY category, stat_key, uuid")
_BOARD_AT = (
    "SELECT server_key, uuid, participant_kind, objective, value, MAX(at) AS at "
    "FROM scoreboard_snapshots INDEXED BY idx_scoreboard_point "
    "WHERE server_key = ? AND at <= ? AND uuid IS NOT NULL GROUP BY objective, uuid")


def _point_sources(key, at):
    """Row sources (same shape as _sources) of counted players' values > 0
    at time `at` (stats, then scoreboards as category 'scoreboard')."""
    fs, ps = counted_stats(key, "s")
    fr, pr = counted_scoreboard(key, "ss", False)
    return [
        ("SELECT s.server_key, s.uuid, s.category, s.stat_key, s.value "
         f"FROM ({_STAT_AT}) s WHERE {fs} AND s.value > 0", [key, at, *ps], "0"),
        (f"SELECT ss.server_key, ss.uuid, '{SCOREBOARD_CATEGORY}' AS category, "
         "ss.objective AS stat_key, ss.value "
         f"FROM ({_BOARD_AT}) ss WHERE {fr} AND ss.value > 0", [key, at, *pr], "0"),
    ]


def _materialize_point(conn, key, at, table):
    """temp.<table> := _point_sources(key, at), read once per point and then
    reused for firsts, crossings and as the next point's `prev`."""
    conn.execute(f"DROP TABLE IF EXISTS temp.{table}")
    conn.execute(f"CREATE TEMP TABLE {table} (server_key TEXT, uuid TEXT, category TEXT, "
                 "stat_key TEXT, value INTEGER, PRIMARY KEY (category, stat_key, uuid)) "
                 "WITHOUT ROWID")
    for src, params, _ in _point_sources(key, at):
        conn.execute(f"INSERT INTO temp.{table} {src}", params)
    return [(f"SELECT server_key, uuid, category, stat_key, value FROM temp.{table}", [], "0")]


def _pair_source(table, prev_table):
    """Rows of point q (table) with their value at the previous point p
    (prev_table) as `prev`, 0 if none; the crossing SQL reads x.prev."""
    return [(f"SELECT q.server_key, q.uuid, q.category, q.stat_key, q.value, "
             f"COALESCE(p.value, 0) AS prev FROM temp.{table} q LEFT JOIN temp.{prev_table} p "
             "ON p.category = q.category AND p.stat_key = q.stat_key AND p.uuid = q.uuid "
             "WHERE q.value > COALESCE(p.value, 0)", [], "x.prev")]


# ----------------------------------------------------------------------- firsts

_TOP_HOLDER = (
    "INSERT OR IGNORE INTO firsts (at, server_key, uuid, category, stat_key, earliest_known, "
    "approximate) "
    "SELECT ?, server_key, uuid, category, stat_key, ?, ? FROM ("
    " SELECT server_key, uuid, category, stat_key, ROW_NUMBER() OVER ("
    "  PARTITION BY category, stat_key ORDER BY value DESC, uuid) AS rn FROM ({src})"
    ") WHERE rn = 1")


def derive_firsts(conn, run_at):
    # Known limitation: post-seed firsts come only from rows written at
    # run_at. If a derive run fails, its changes are not retried -- the next
    # run diffs only its own rows (a later change to the key still yields a
    # first, possibly for a different player).
    for key, seed in _seed_modes(conn, run_at).items():
        # Seed: top current holder of every key with no firsts row, flagged
        # earliest_known=1. After seeding: a key with no row that changed this
        # run goes to the top holder among this run's rows, earliest_known=0.
        kind, ek = ("current", 1) if seed else ("run", 0)
        _insert_firsts(conn, _sources(key, run_at)[kind], run_at, ek)
    conn.commit()


def _insert_firsts(conn, sources, at, ek, approximate=0):
    """Top holder of every key in `sources` that has no firsts row yet."""
    for src, params, _ in sources:
        conn.execute(_TOP_HOLDER.format(src=src), [at, ek, approximate, *params])


# ------------------------------------------------------------------- milestones

_CROSSING = (
    "INSERT OR IGNORE INTO milestones (at, server_key, uuid, rule_key, category, stat_key, "
    "threshold, value_at, earliest_known, approximate) "
    "SELECT ?, x.server_key, x.uuid, r.rule_key, x.category, x.stat_key, t.value, x.value, ?, ? "
    "FROM ({src}) x "
    "JOIN milestone_rules r ON r.enabled = 1 AND r.category = x.category "
    " AND (r.stat_key IS NULL OR r.stat_key = x.stat_key) "
    "JOIN json_each(r.thresholds) t ON t.value <= x.value AND t.value > {prev}")


def sync_milestone_rules(conn, settings=None):
    """Upsert the configured rules (thresholds updated, enabled=1) and set
    enabled=0 on rules no longer configured. Never deletes: milestones rows
    keep their rule_key. New thresholds are not backfilled -- crossings are
    only detected from changes after the rule exists."""
    rules = (settings or DEFAULTS).milestones.rules()
    conn.executemany(
        "INSERT INTO milestone_rules (rule_key, category, stat_key, thresholds, enabled) "
        "VALUES (?, ?, ?, ?, 1) ON CONFLICT(rule_key) DO UPDATE SET "
        "category=excluded.category, stat_key=excluded.stat_key, "
        "thresholds=excluded.thresholds, enabled=1",
        [(rk, c, k, json.dumps(t)) for rk, c, k, t in rules])
    keys = [r[0] for r in rules]
    if keys:
        conn.execute("UPDATE milestone_rules SET enabled=0 WHERE enabled=1 AND rule_key "
                     f"NOT IN ({','.join('?' * len(keys))})", keys)
    else:
        conn.execute("UPDATE milestone_rules SET enabled=0")


def derive_milestones(conn, run_at, settings=None):
    # Known limitation: crossings are detected from rows written at run_at
    # only. If a derive run fails, that run's crossings are never recorded --
    # the next run diffs only its own changes against their previous rows.
    sync_milestone_rules(conn, settings)
    seeded = _seeded(conn)
    for key, seed in _seed_modes(conn, run_at).items():
        if seed:
            # every counted current value >= threshold, "already had"
            _insert_crossings(conn, _sources(key, run_at)["current"], run_at, 1, prev="0")
        else:
            # prev = latest row at < run_at (else 0); prev < threshold <= new
            _insert_crossings(conn, _sources(key, run_at)["run"], run_at, 0,
                              prev_params=[run_at - 1])
    conn.commit()
    if not seeded:
        # Only after the seeding above has committed (firsts committed earlier
        # in the same run_derive).
        conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'", (SEED_MARKER,))
        conn.commit()


def _insert_crossings(conn, sources, at, ek, approximate=0, prev=None, prev_params=()):
    """Milestones with prev < threshold <= value for every row of `sources`
    (prev: SQL over x; default the source's own prev expression)."""
    for src, params, src_prev in sources:
        conn.execute(_CROSSING.format(src=src, prev=src_prev if prev is None else prev),
                     [at, ek, approximate, *params, *prev_params])


# ----------------------------------------------------------------------- badges

def _local_hour(ts):
    return time.localtime(ts).tm_hour


def _local_day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _award(conn, key, run_at, select_sql, params):
    """select_sql yields (uuid, badge_key); insert-if-new, never deleted."""
    conn.execute(
        "INSERT OR IGNORE INTO badges (uuid, badge_key, awarded_at, server_key) "
        f"SELECT uuid, badge_key, ?, ? FROM ({select_sql})", [run_at, key, *params])


def _tiers(count_sql, label_sql):
    """count_sql yields (uuid, n[, category]); joined against the JSON tier
    list passed as the last param, aliased t (t.value threshold, t.key index)."""
    return (f"SELECT c.uuid, {label_sql} AS badge_key FROM ({count_sql}) c "
            "JOIN json_each(?) t ON c.n >= t.value")


def derive_badges(conn, run_at, settings=None):
    _derive_badges(conn, run_at, settings)
    conn.commit()


def _derive_badges(conn, run_at, settings=None):
    settings = settings or DEFAULTS
    b = settings.badges
    cats = settings.milestones.count_categories
    play_cat, play_key = settings.activity_stat
    veteran_ticks = int(round(b.veteran_hours * TICKS_PER_HOUR))
    regular_since = run_at - days_s(b.regular.window_days)
    owl_since = run_at - days_s(b.night_owl.window_days)
    conn.create_function("chronos_local_hour", 1, _local_hour, deterministic=True)
    conn.create_function("chronos_local_day", 1, _local_day, deterministic=True)
    for key in _servers(conn):
        fs, ps = counted_stats(key, "s")
        ff, pf = counted_stats(key, "f")
        fm, pm = counted_stats(key, "m")
        fa, pa = counted_stats(key, "a")

        # Firsts: only earliest_known=0 rows count (approximate=1 rows are
        # real observed firsts, dated "by" a backup, so they count).
        _award(conn, key, run_at, _tiers(
            f"SELECT f.uuid, COUNT(*) AS n FROM firsts f WHERE {ff} AND f.earliest_known = 0 "
            "GROUP BY f.uuid", "'firsts:' || t.value"), [*pf, json.dumps(b.firsts)])

        # Champion: #1 (ties included) in N stat keys at the same time.
        _award(conn, key, run_at, _tiers(
            "SELECT uuid, COUNT(*) AS n FROM (SELECT s.uuid, RANK() OVER ("
            " PARTITION BY s.category, s.stat_key ORDER BY s.value DESC) AS rk "
            f" FROM stat_current s WHERE {fs} AND s.value > 0) WHERE rk = 1 GROUP BY uuid",
            "'top1:' || t.value"), [*ps, json.dumps(b.top1)])

        # Big numbers: a milestone row at each big_numbers threshold, either
        # earliest_known flag, from count rules only (cat:*) -- unit-bearing
        # rules (play time, distance, deaths) never read as "Ten-Millionaire".
        # Only thresholds that are also milestones.count_thresholds can match.
        if b.big_numbers:
            marks = ",".join("?" * len(b.big_numbers))
            _award(conn, key, run_at,
                   "SELECT DISTINCT m.uuid, 'milestone:' || m.threshold AS badge_key "
                   f"FROM milestones m WHERE {fm} AND m.rule_key LIKE 'cat:%' "
                   f"AND m.threshold IN ({marks})", [*pm, *b.big_numbers])

        # Category tiers: sum over all keys in each milestones.count_categories.
        if cats:
            marks = ",".join("?" * len(cats))
            _award(conn, key, run_at, _tiers(
                "SELECT s.uuid, s.category, SUM(s.value) AS n FROM stat_current s "
                f"WHERE {fs} AND s.category IN ({marks}) GROUP BY s.uuid, s.category",
                "'cat:' || c.category || ':' || (t.key + 1)"),
                [*ps, *cats, json.dumps(b.category_tiers)])

        # Veteran: activity_stat (play time, ticks) >= veteran_hours.
        _award(conn, key, run_at,
               "SELECT s.uuid, 'veteran' AS badge_key FROM stat_current s "
               f"WHERE {fs} AND s.category = ? AND s.stat_key = ? AND s.value >= ?",
               [*ps, play_cat, play_key, veteran_ticks])

        recent = f"FROM activity a WHERE {fa} AND a.at > ? AND a.at <= ? GROUP BY a.uuid"
        # Regular: active on >= regular.days distinct (server-local) days of
        # the last regular.window_days.
        _award(conn, key, run_at,
               f"SELECT a.uuid, 'regular' AS badge_key {recent} "
               "HAVING COUNT(DISTINCT chronos_local_day(a.at)) >= ?",
               [*pa, regular_since, run_at, b.regular.days])
        # Night Owl: >= min_buckets buckets in the last window_days, more than
        # `share` of them in [start_hour, end_hour) server-local.
        _award(conn, key, run_at,
               f"SELECT a.uuid, 'night_owl' AS badge_key {recent} "
               "HAVING COUNT(*) >= ? AND SUM(chronos_local_hour(a.at) >= ? "
               "AND chronos_local_hour(a.at) < ?) > ? * COUNT(*)",
               [*pa, owl_since, run_at, b.night_owl.min_buckets, b.night_owl.start_hour,
                b.night_owl.end_hour, b.night_owl.share])


# ------------------------------------------------------------ backup import

def _points(conn, key):
    """Imported backup dates plus the first live stats run, oldest first."""
    rows = conn.execute(
        "SELECT DISTINCT at FROM snapshot_runs WHERE server_key=? AND ok=1 AND entries_read>0 "
        "AND (kind='import' OR (kind='stats' AND at=(SELECT MIN(at) FROM snapshot_runs "
        " WHERE server_key=? AND kind='stats' AND ok=1 AND entries_read>0))) ORDER BY at",
        (key, key))
    return [r[0] for r in rows]


def rebuild_from_points(conn, server_key, settings, *, lock_timeout_s=BUSY_TIMEOUT_S):
    """Re-derive firsts/milestones/totals/badges of server_key over its
    points (imported backups + the first live stats run). Called by
    tools/import_backups.py after an import; the only writer of
    approximate=1 rows.

    - Deletes the server's earliest_known=1 / approximate=1 firsts and
      milestones; real live rows (both 0) are never touched, and every insert
      is OR IGNORE, so they win.
    - Oldest point: everything true there -> earliest_known=1 (as seeding).
    - Each later point q (after p): keys first held / thresholds crossed
      between p and q -> dated q, approximate=1.
    - Then, as the live seeding did, current values not explained by the
      points or by live rows (live history between the first live run and
      the first derive) -> earliest_known=1 at the latest live run.
    - Totals: totals_backfilled reset and the backfill rerun (all history).
    - Badges: the badge stage. Meta: seeded_achievements=1, tracking start.

    Takes its own write lock (BEGIN IMMEDIATE; DatabaseBusy if a snapshot
    holds it) and runs as one transaction. Returns row counts."""
    if conn.in_transaction:
        raise RuntimeError("rebuild_from_points needs a connection with no open transaction")
    begin_immediate(conn, lock_timeout_s)
    try:
        counts = _rebuild(conn, server_key, settings or DEFAULTS)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return counts


def _rebuild(conn, key, settings):
    import derive  # derive imports this module; import lazily

    def changes(fn, *args, **kw):
        before = conn.total_changes
        fn(*args, **kw)
        return conn.total_changes - before

    counts = {"points": 0, "firsts": 0, "milestones": 0, "badges": 0, "totals": 0}
    points = _points(conn, key)
    if not points:
        return counts
    counts["points"] = len(points)
    latest_live = conn.execute(
        "SELECT MAX(at) FROM snapshot_runs WHERE server_key=? AND kind='stats' AND ok=1 "
        "AND entries_read>0", (key,)).fetchone()[0]

    sync_milestone_rules(conn, settings)
    # earliest_known rows after the last point came from seeding (live, or a
    # previous rebuild's end pass): keep their original date across rebuilds.
    conn.execute("DROP TABLE IF EXISTS temp.chronos_keep_f")
    conn.execute("DROP TABLE IF EXISTS temp.chronos_keep_m")
    conn.execute("CREATE TEMP TABLE chronos_keep_f AS SELECT category, stat_key, at FROM firsts "
                 "WHERE server_key=? AND earliest_known=1 AND at>?", (key, points[-1]))
    conn.execute("CREATE TEMP TABLE chronos_keep_m AS SELECT uuid, category, stat_key, "
                 "threshold, at FROM milestones WHERE server_key=? AND earliest_known=1 "
                 "AND at>?", (key, points[-1]))
    for t in ("firsts", "milestones"):
        conn.execute(f"DELETE FROM {t} WHERE server_key=? AND "
                     "(earliest_known=1 OR approximate=1)", (key,))

    def firsts(*a, **kw):
        counts["firsts"] += changes(_insert_firsts, conn, *a, **kw)

    def crossings(*a, **kw):
        counts["milestones"] += changes(_insert_crossings, conn, *a, **kw)

    # Oldest point: "already had" (earliest_known=1), like seeding.
    cur, prev = "chronos_point_a", "chronos_point_b"
    p0 = points[0]
    src = _materialize_point(conn, key, p0, cur)
    firsts(src, p0, 1)
    crossings(src, p0, 1, prev="0")
    # Between points: keys with a holder at p already have a firsts row (from
    # an earlier point or a live row), so OR IGNORE leaves exactly the keys
    # first held between p and q.
    for q in points[1:]:
        cur, prev = prev, cur
        src = _materialize_point(conn, key, q, cur)
        firsts(src, q, 0, approximate=1)
        crossings(_pair_source(cur, prev), q, 0, approximate=1)
    for t in (cur, prev):
        conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    if latest_live is not None and latest_live > points[-1]:
        current = _sources(key, latest_live)["current"]
        firsts(current, latest_live, 1)
        crossings(current, latest_live, 1, prev="0")
        conn.execute(
            "UPDATE firsts SET at=(SELECT k.at FROM temp.chronos_keep_f k WHERE "
            "k.category=firsts.category AND k.stat_key=firsts.stat_key) "
            "WHERE server_key=? AND earliest_known=1 AND at=? AND EXISTS (SELECT 1 FROM "
            "temp.chronos_keep_f k WHERE k.category=firsts.category "
            "AND k.stat_key=firsts.stat_key)", (key, latest_live))
        conn.execute(
            "UPDATE milestones SET at=(SELECT k.at FROM temp.chronos_keep_m k WHERE "
            "k.uuid=milestones.uuid AND k.category=milestones.category AND "
            "k.stat_key IS milestones.stat_key AND k.threshold=milestones.threshold) "
            "WHERE server_key=? AND earliest_known=1 AND at=? AND EXISTS (SELECT 1 FROM "
            "temp.chronos_keep_m k WHERE k.uuid=milestones.uuid AND "
            "k.category=milestones.category AND k.stat_key IS milestones.stat_key "
            "AND k.threshold=milestones.threshold)", (key, latest_live))
    conn.execute("DROP TABLE temp.chronos_keep_f")
    conn.execute("DROP TABLE temp.chronos_keep_m")
    conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') "
                 "ON CONFLICT(key) DO UPDATE SET value='1'", (SEED_MARKER,))

    # Totals history from the oldest point: rerun the one-time backfill over
    # all history (end = past the newest row, so every point is emitted).
    end = 1 + max(conn.execute("SELECT COALESCE(MAX(at), 0) FROM stat_snapshots").fetchone()[0],
                  conn.execute("SELECT COALESCE(MAX(at), 0) FROM scoreboard_snapshots"
                               ).fetchone()[0])
    conn.execute("DELETE FROM meta WHERE key=?", (derive.BACKFILL_MARKER,))
    derive._backfill(conn, end)
    counts["totals"] = sum(conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE server_key=?", (key,)).fetchone()[0]
        for t in ("stat_totals", "scoreboard_totals"))

    now = latest_live if latest_live is not None else points[-1]
    counts["badges"] = changes(_derive_badges, conn, now, settings)
    row = conn.execute("SELECT value FROM meta WHERE key='last_run_at'").fetchone()
    derive._write_meta(conn, int(row[0]) if row else now)
    return counts
