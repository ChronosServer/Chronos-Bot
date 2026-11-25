"""Where a server's history is dense (live) and where it is sparse (imports).

Before a server's first live stats run L, history exists only at backup
import points, months apart. A range starting at T < L must be measured
from the next real observation -- the smallest import point >= T, or L --
never from the latest backup before T, or a "last week" gain spans years.
From L on, live history is dense and T is used as is.

derive writes the points to meta (history_points:<server> = JSON list of
the import points plus L, sorted; live_started_at:<server> = L); the API
(timeq.parse_range, rival windows) reads them from there, and derive
(clamped_ago) computes them from snapshot_runs. Pure stdlib: derive imports
this module.
"""
import json


def snap_from(from_ts, points, live):
    """Effective start of a range beginning at from_ts on one server.

    points: sorted history points (imports + L); live: L (None if no live
    run yet). Before the first point (pre-tracking: the caller's clamp to
    tracking start handles it) and from L on: from_ts unchanged. In between:
    the smallest point >= from_ts."""
    if not points or from_ts < points[0]:
        return from_ts
    if live is not None and from_ts >= live:
        return from_ts
    for p in points:
        if p >= from_ts:
            return p
    return from_ts


def meta_points(conn, server):
    """(points, live) from meta; ([], None) when derive hasn't written them
    (then snap_from changes nothing)."""
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT key, value FROM meta WHERE key IN (?, ?)",
        (f"history_points:{server}", f"live_started_at:{server}"))}
    raw = rows.get(f"history_points:{server}")
    live = rows.get(f"live_started_at:{server}")
    try:
        points = sorted(int(p) for p in json.loads(raw)) if raw else []
    except (ValueError, TypeError):
        points = []
    return points, (int(live) if live is not None else None)


def snap_scope(conn, servers, from_ts):
    """Max over the servers of each one's snapped start (from_ts if none)."""
    return max([from_ts] + [snap_from(from_ts, *meta_points(conn, s)) for s in servers])
