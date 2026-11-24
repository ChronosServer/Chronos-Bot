"""Derive stages: firsts, milestones, badges (spec "Derived data" + Addendum A1).

Seeding: on the first derive where meta `seeded_achievements` is absent,
everything that already exists is recorded with earliest_known=1 ("already
had", no fake date). A server's own first qualifying run is treated the same
way (every row is "changed" then, which is not an observed achievement).
All writes are INSERT OR IGNORE against unique indexes, so re-running a
derive for the same run_at never duplicates anything.
"""
import json
import time

from derive_common import (
    DEFAULTS, SCOREBOARD_CATEGORY, TICKS_PER_HOUR, _servers, counted_scoreboard,
    counted_stats, days_s, stat_value_at_sql, tracking_start,
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


# ----------------------------------------------------------------------- firsts

_TOP_HOLDER = (
    "INSERT OR IGNORE INTO firsts (at, server_key, uuid, category, stat_key, earliest_known) "
    "SELECT ?, server_key, uuid, category, stat_key, ? FROM ("
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
        for src, params, _ in _sources(key, run_at)[kind]:
            conn.execute(_TOP_HOLDER.format(src=src), [run_at, ek, *params])
    conn.commit()


# ------------------------------------------------------------------- milestones

_CROSSING = (
    "INSERT OR IGNORE INTO milestones (at, server_key, uuid, rule_key, category, stat_key, "
    "threshold, value_at, earliest_known) "
    "SELECT ?, x.server_key, x.uuid, r.rule_key, x.category, x.stat_key, t.value, x.value, ? "
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
            for src, params, _ in _sources(key, run_at)["current"]:
                conn.execute(_CROSSING.format(src=src, prev="0"), [run_at, 1, *params])
        else:
            # prev = latest row at < run_at (else 0); prev < threshold <= new
            for src, params, prev in _sources(key, run_at)["run"]:
                conn.execute(_CROSSING.format(src=src, prev=prev),
                             [run_at, 0, *params, run_at - 1])
    conn.commit()
    if not seeded:
        # Only after the seeding above has committed (firsts committed earlier
        # in the same run_derive).
        conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'", (SEED_MARKER,))
        conn.commit()


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

        # Firsts: only earliest_known=0 rows count.
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
    conn.commit()


# ------------------------------------------------------------ backup import

def rebuild_from_points(conn, server_key, settings):
    """Re-derive firsts/milestones/totals/badges for server_key over the
    imported backup points (tools/import_backups.py calls it after a
    non-dry import). Stub until the backup-import Task 3 lands: no-op."""
    return {}
