"""Shared rivalry maths (/player rivals, /rivalries)."""
from typing import Optional, Tuple

from api.history import meta_points, snap_from
from api.settings import DAY

# Fixed, not tuning: the window is what gap_7d_ago means (bot/derive_common.py
# WEEK), and the floor is one 15-minute snapshot interval.
WINDOW = 7 * DAY
MIN_WINDOW_DAYS = 1 / 96


def closing_stats(gap: int, gap_7d_ago: Optional[int], computed_at: int,
                  tracking_started_at: Optional[int]
                  ) -> Tuple[Optional[float], Optional[float]]:
    """(closing_per_day, overtake_days). The comparison window is 7 days,
    clamped to tracking_started_at -- pass window_floor(): tracking start,
    snapped to where gap_7d_ago was really measured -- never a fixed 7."""
    if gap_7d_ago is None:
        return None, None
    start = computed_at - WINDOW
    if tracking_started_at is not None:
        start = max(start, tracking_started_at)
    days = max((computed_at - start) / DAY, MIN_WINDOW_DAYS)
    closing = (gap_7d_ago - gap) / days
    return closing, (gap / closing if closing > 0 else None)


def window_floor(conn, server_key: str, computed_at: int, tracking_started_at: Optional[int],
                 cache: dict) -> Optional[int]:
    """Where derive measured gap_7d_ago (derive_common.clamped_ago): 7 days
    before computed_at, clamped to tracking start, snapped forward to the
    next history point before live tracking. The closing_stats floor."""
    if server_key not in cache:
        cache[server_key] = meta_points(conn, server_key)
    if tracking_started_at is None:
        return None
    return snap_from(max(computed_at - WINDOW, tracking_started_at), *cache[server_key])
