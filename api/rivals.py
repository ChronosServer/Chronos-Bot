"""Shared rivalry maths (/player rivals, /rivalries)."""
from typing import Optional, Tuple

WINDOW = 7 * 86400
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
    days = max((computed_at - start) / 86400, MIN_WINDOW_DAYS)
    closing = (gap_7d_ago - gap) / days
    return closing, (gap / closing if closing > 0 else None)
