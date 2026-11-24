"""Time engine over the change-only history tables.

Value at T = latest row with at <= T per (server, uuid, key). History is sparse,
so `at` is never lower-bounded: a player whose last change predates the range
still has a value at its start.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Query

from api.history import snap_scope
from api.scope import (Scope, scoreboard_filter_sql, stat_filter_sql)
from api.settings import api_settings

# Bound for `from`/`to` query params: far past any real timestamp, well inside
# SQLite's int64, so absurd values are a 422 instead of an overflow 500.
TS_MAX = 2 ** 62


def ts_param(alias=None):
    return Query(None, ge=0, le=TS_MAX, alias=alias)


@dataclass
class Range:
    from_ts: int
    to_ts: int
    effective_from: int
    is_now: bool

    @property
    def empty(self) -> bool:
        """True when the window ends before tracking began: no data exists."""
        return self.effective_from > self.to_ts


def tracking_start(conn, scope: Scope) -> Optional[int]:
    vals = []
    for s in scope.servers:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           (f"tracking_started_at:{s}",)).fetchone()
        if row and row[0] is not None:
            vals.append(int(row[0]))
    return min(vals) if vals else None


def parse_range(conn, scope: Scope, range: Optional[str], from_: Optional[int],
                to: Optional[int], now: int, presets=None) -> Range:
    """presets: name -> seconds (default: settings.api.range_presets);
    "all" is always valid."""
    if presets is None:
        presets = api_settings().api.range_presets
    start = tracking_start(conn, scope)
    to_ts = now if to is None else int(to)
    if range is not None:
        if range == "all":
            from_ts = start if start is not None else 0
        elif range in presets:
            from_ts = to_ts - presets[range]
        else:
            raise ValueError(f"bad range: {range!r}")
    elif from_ is not None:
        from_ts = int(from_)
    else:
        from_ts = start if start is not None else 0
    if from_ts > to_ts:
        raise ValueError("from must be <= to")
    eff = from_ts if start is None else max(from_ts, start)
    # Before live tracking a server's history is sparse (import points only):
    # start at the next real observation, never a stale earlier backup.
    eff = max(eff, snap_scope(conn, scope.servers, from_ts))
    return Range(from_ts, to_ts, eff, to is None)


def _sum(rows) -> dict:
    out: dict = {}
    for uuid, value in rows:
        out[uuid] = out.get(uuid, 0) + value
    return out


# INDEXED BY: without ANALYZE the planner prefers (server_key, at) indexes and
# scans a server's whole history instead of seeking one key.
def _in(col: str, uuids):
    if uuids is None:
        return "", []
    return f" AND {col} IN ({','.join('?' * len(uuids))})", list(uuids)


def stat_history_sql(frag: str, uuid_sql: str = "") -> str:
    return (f"SELECT s.uuid, s.value, MAX(s.at) FROM stat_snapshots s "
            f"INDEXED BY idx_stat_point WHERE s.category=? AND s.stat_key=? "
            f"AND s.at<=? AND {frag}{uuid_sql} GROUP BY s.server_key, s.uuid")


def scoreboard_history_sql(frag: str, uuid_sql: str = "") -> str:
    return (f"SELECT s.uuid, s.value, MAX(s.at) FROM scoreboard_snapshots s "
            f"INDEXED BY idx_scoreboard_point WHERE s.objective=? AND s.at<=? "
            f"AND s.uuid IS NOT NULL AND {frag}{uuid_sql} GROUP BY s.server_key, s.uuid")


def stat_values_at(conn, scope: Scope, include_bots: bool, category: str,
                   key: str, ts: Optional[int], uuids=None) -> dict:
    frag, params = stat_filter_sql(scope, include_bots, "s")
    usql, up = _in("s.uuid", uuids)
    if ts is None:
        rows = conn.execute(
            f"SELECT s.uuid, s.value FROM stat_current s "
            f"INDEXED BY idx_stat_current_key WHERE s.category=? "
            f"AND s.stat_key=? AND {frag}{usql}", [category, key, *params, *up]).fetchall()
        return _sum(rows)
    rows = conn.execute(stat_history_sql(frag, usql),
                        [category, key, ts, *params, *up]).fetchall()
    return _sum((r[0], r[1]) for r in rows)


def scoreboard_values_at(conn, scope: Scope, include_bots: bool, objective: str,
                         ts: Optional[int], uuids=None) -> dict:
    if ts is None:
        frag, params = scoreboard_filter_sql(scope, include_bots, "s", is_current=True)
        usql, up = _in("s.participant_key", uuids)
        rows = conn.execute(
            f"SELECT s.participant_key, s.value FROM scoreboard_current s "
            f"INDEXED BY idx_scoreboard_current_obj WHERE s.objective=? "
            f"AND {frag}{usql}", [objective, *params, *up]).fetchall()
        return _sum(rows)
    frag, params = scoreboard_filter_sql(scope, include_bots, "s")
    usql, up = _in("s.uuid", uuids)
    rows = conn.execute(scoreboard_history_sql(frag, usql),
                        [objective, ts, *params, *up]).fetchall()
    return _sum((r[0], r[1]) for r in rows)


def downsample(points, from_ts: int, to_ts: int, max_points: Optional[int] = None):
    """Step-preserving: first point is (from_ts, value at from_ts); later points
    keep the last value per time bucket. Result length <= max_points
    (default: settings.api.series_max_points)."""
    if max_points is None:
        max_points = api_settings().api.series_max_points
    pts = sorted(points)
    v0 = 0
    inside = []
    for t, v in pts:
        if t <= from_ts:
            v0 = v
        elif t <= to_ts:
            inside.append((t, v))
    first = (from_ts, v0)
    if len(inside) + 1 <= max_points:
        return [first] + inside
    nb = max_points - 1
    span = max(to_ts - from_ts, 1)
    buckets: dict = {}
    for t, v in inside:
        b = min((t - from_ts) * nb // span, nb - 1)
        buckets[b] = (t, v)
    return [first] + [buckets[b] for b in sorted(buckets)]
