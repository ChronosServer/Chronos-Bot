"""Shared rivalry maths (/player rivals, /rivalries)."""
from typing import Optional, Tuple

from api.settings import DAY

# Fixed, not tuning: the window is what gap_7d_ago means (bot/derive_common.py
# WEEK), and the floor is one 15-minute snapshot interval.
WINDOW = 7 * DAY
MIN_WINDOW_DAYS = 1 / 96


def closing_stats(gap: int, gap_7d_ago: Optional[int], computed_at: int,
                  tracking_started_at: Optional[int]
                  ) -> Tuple[Optional[float], Optional[float]]:
    """(closing_per_day, overtake_days). The comparison window is 7 days,
    clamped to when tracking began on that server, never a fixed 7."""
    if gap_7d_ago is None:
        return None, None
    start = computed_at - WINDOW
    if tracking_started_at is not None:
        start = max(start, tracking_started_at)
    days = max((computed_at - start) / DAY, MIN_WINDOW_DAYS)
    closing = (gap_7d_ago - gap) / days
    return closing, (gap / closing if closing > 0 else None)
