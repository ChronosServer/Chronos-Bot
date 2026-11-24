"""Derive stages: rivalries (spec Addendum A2) and spotlight candidates (A3).
Counted players only; stat-file stats only (not scoreboards).
"""
import json
from collections import Counter

from derive_common import (
    DAY, DEFAULTS, WEEK, _servers, clamped_ago, counted_all, counted_stats, days_s,
    stat_value_at_sql,
)

# Thresholds: settings.rivalries / settings.spotlight (config.json
# stats_tracking; defaults in api/settings.py).


# -------------------------------------------------------------------- rivalries

def _award_overtakers(conn, run_at):
    """Stored pair whose B is now >= A: B passed the player above them. A pair
    stored as a tie that is still a tie hasn't flipped, so it doesn't count."""
    fb, pb = counted_all(conn, "b")
    conn.execute(
        "INSERT OR IGNORE INTO badges (uuid, badge_key, awarded_at, server_key) "
        "SELECT DISTINCT r.uuid_b, 'overtaker', ?, r.server_key FROM rivalries r "
        "JOIN stat_current a ON a.server_key = r.server_key AND a.uuid = r.uuid_a "
        " AND a.category = r.category AND a.stat_key = r.stat_key "
        "JOIN stat_current b ON b.server_key = r.server_key AND b.uuid = r.uuid_b "
        " AND b.category = r.category AND b.stat_key = r.stat_key "
        f"WHERE {fb} AND b.value >= a.value AND NOT (b.value = a.value AND r.gap = 0)",
        [run_at, *pb])


def _rivalry_candidates(conn, key, run_at, rv):
    """Adjacent counted pairs (A directly above B) passing every A2 filter.
    rv: settings.rivalries. The gap comparison window stays 7 days (WEEK):
    it is what gap_7d_ago means."""
    then = clamped_ago(conn, key, run_at, WEEK)
    if then is None:
        return []
    fs, ps = counted_stats(key, "s")
    rows = conn.execute(
        "WITH ranked AS ("
        " SELECT s.server_key, s.category, s.stat_key, s.uuid, s.value,"
        "  LEAD(s.uuid) OVER w AS uuid_b, LEAD(s.value) OVER w AS value_b"
        f" FROM stat_current s WHERE {fs} AND s.value > 0"
        " WINDOW w AS (PARTITION BY s.category, s.stat_key ORDER BY s.value DESC, s.uuid)),"
        " act AS (SELECT DISTINCT uuid FROM activity WHERE server_key = ? AND at >= ? AND at <= ?),"
        " pairs AS ("
        "  SELECT server_key, category, stat_key, uuid, value, uuid_b, value_b FROM ranked"
        "  WHERE uuid_b IS NOT NULL AND value >= ? AND (value - value_b) <= ? * value"
        "  AND uuid IN (SELECT uuid FROM act) AND uuid_b IN (SELECT uuid FROM act))"
        f" SELECT p.category, p.stat_key, p.uuid, p.uuid_b, p.value, p.value_b,"
        f"  {stat_value_at_sql('p', 'uuid')}, {stat_value_at_sql('p', 'uuid_b')}"
        " FROM pairs p",
        [*ps, key, run_at - days_s(rv.active_days), run_at,
         rv.min_value, rv.max_gap_ratio, then, then]).fetchall()
    out = []
    for cat, sk, ua, ub, va, vb, a_then, b_then in rows:
        gap, gap_ago = va - vb, a_then - b_then
        # The race is moving: gap changed and B gained on this stat this week.
        if gap == gap_ago or vb - b_then < 1:
            continue
        closing = (gap_ago - gap) / (WEEK / DAY)
        overtake_days = gap / closing if closing > 0 else None
        out.append({"category": cat, "stat_key": sk, "a": ua, "b": ub, "gap": gap,
                    "gap_7d_ago": gap_ago, "overtake_days": overtake_days,
                    "rel_gap": gap / va})
    return out


def _cap(cands, rv):
    """Per-key cap, then per-player cap, both in order of lowest overtake_days
    (None last), then smallest relative gap."""
    cands.sort(key=lambda c: (c["overtake_days"] is None, c["overtake_days"] or 0,
                              c["rel_gap"], c["category"], c["stat_key"], c["a"]))
    per_key, kept_key = Counter(), []
    for c in cands:
        k = (c["category"], c["stat_key"])
        if per_key[k] < rv.per_key:
            per_key[k] += 1
            kept_key.append(c)
    per_player, kept = Counter(), []
    for c in kept_key:
        if per_player[c["a"]] < rv.per_player and per_player[c["b"]] < rv.per_player:
            per_player[c["a"]] += 1
            per_player[c["b"]] += 1
            kept.append(c)
    return kept


def derive_rivalries(conn, run_at, settings=None):
    rv = (settings or DEFAULTS).rivalries
    _award_overtakers(conn, run_at)
    conn.execute("DELETE FROM rivalries")
    for key in _servers(conn):
        kept = _cap(_rivalry_candidates(conn, key, run_at, rv), rv)
        conn.executemany(
            "INSERT INTO rivalries (server_key, category, stat_key, uuid_a, uuid_b, gap, "
            "gap_7d_ago, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(key, c["category"], c["stat_key"], c["a"], c["b"], c["gap"], c["gap_7d_ago"],
              run_at) for c in kept])
    conn.commit()


# -------------------------------------------------------------------- spotlight

_SPOT = ("INSERT INTO spotlight_candidates (trigger, server_key, uuid, category, stat_key, "
         "value, detail_json, computed_at) SELECT ?, ?, uuid, category, stat_key, value, "
         "detail, ? FROM ({sql})")


def _spot(conn, trigger, key, run_at, sql, params):
    conn.execute(_SPOT.format(sql=sql), [trigger, key, run_at, *params])


def _holders(filt):
    return (
        "SELECT s.uuid, s.category, s.stat_key, s.value,"
        " ROW_NUMBER() OVER (PARTITION BY s.category, s.stat_key ORDER BY s.value DESC, s.uuid) AS rn,"
        " COUNT(*) OVER (PARTITION BY s.category, s.stat_key) AS n"
        f" FROM stat_current s WHERE {filt} AND s.value > 0")


def derive_spotlight(conn, run_at, settings=None):
    sp = (settings or DEFAULTS).spotlight
    conn.execute("DELETE FROM spotlight_candidates")
    for key in _servers(conn):
        fs, ps = counted_stats(key, "s")
        fm, pm = counted_stats(key, "m")
        ff, pf = counted_stats(key, "f")

        # outlier: the top counted holder (one per key), if their value is
        # >= outlier.ratio x the median of non-zero holders, with
        # >= outlier.min_holders holders.
        # rn is descending, so the median sits at rn (n+1)/2 and (n+2)/2.
        _spot(conn, "outlier", key, run_at,
              f"WITH h AS ({_holders(fs)}),"
              " med AS (SELECT category, stat_key, n, AVG(value) AS m FROM h"
              "  WHERE n >= ? AND rn IN ((n + 1) / 2, (n + 2) / 2) GROUP BY category, stat_key, n)"
              " SELECT h.uuid, h.category, h.stat_key, h.value,"
              "  json_object('median', med.m, 'ratio', ROUND(h.value / med.m, 1), 'holders', med.n)"
              "  AS detail FROM h JOIN med ON med.category = h.category AND med.stat_key = h.stat_key"
              "  WHERE h.rn = 1 AND h.value >= ? * med.m",
              [*ps, sp.outlier.min_holders, sp.outlier.ratio])

        # rare: held by <= rare.max_holders counted players (value > 0); the top holder.
        _spot(conn, "rare", key, run_at,
              f"SELECT uuid, category, stat_key, value, json_object('holders', n) AS detail"
              f" FROM ({_holders(fs)}) WHERE rn = 1 AND n <= ?",
              [*ps, sp.rare.max_holders])

        # recent_milestone: earliest_known = 0 within recent_milestone_days.
        _spot(conn, "recent_milestone", key, run_at,
              "SELECT m.uuid, m.category, m.stat_key, m.value_at AS value,"
              " json_object('threshold', m.threshold, 'rule_key', m.rule_key, 'at', m.at) AS detail"
              f" FROM milestones m WHERE {fm} AND m.earliest_known = 0 AND m.at >= ?",
              [*pm, run_at - days_s(sp.recent_milestone_days)])

        # recent_first: earliest_known = 0 within recent_first_days.
        _spot(conn, "recent_first", key, run_at,
              "SELECT f.uuid, f.category, f.stat_key, CASE WHEN f.category = 'scoreboard'"
              " THEN (SELECT value FROM scoreboard_current WHERE server_key = f.server_key"
              "  AND participant_key = f.uuid AND objective = f.stat_key)"
              " ELSE (SELECT value FROM stat_current WHERE server_key = f.server_key"
              "  AND uuid = f.uuid AND category = f.category AND stat_key = f.stat_key) END AS value,"
              " json_object('at', f.at) AS detail"
              f" FROM firsts f WHERE {ff} AND f.earliest_known = 0 AND f.at >= ?",
              [*pf, run_at - days_s(sp.recent_first_days)])

        # mover: top mover.top deltas over mover.window_days with
        # delta >= min_delta and >= min_growth x the value at the window
        # start (clamped to tracking start).
        mv = sp.mover
        then = clamped_ago(conn, key, run_at, days_s(mv.window_days))
        if then is not None:
            _spot(conn, "mover", key, run_at,
                  "SELECT uuid, category, stat_key, value,"
                  " json_object('from', then_v, 'delta', value - then_v) AS detail FROM ("
                  f"  SELECT s.uuid, s.category, s.stat_key, s.value, {stat_value_at_sql('s')} AS then_v"
                  f"  FROM stat_current s WHERE {fs} AND s.updated_at > ?)"
                  " WHERE value - then_v >= ? AND (value - then_v) >= ? * then_v"
                  " ORDER BY value - then_v DESC, uuid, category, stat_key LIMIT ?",
                  [then, *ps, then, mv.min_delta, mv.min_growth, mv.top])
    conn.commit()
