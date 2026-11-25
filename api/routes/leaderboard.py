import time

from fastapi import APIRouter, Depends, Request

from api import db, timeq
from api.cache import cached
from api.scope import resolve_scope
from api.settings import api_settings

router = APIRouter()


@router.get("/leaderboard")
@cached
def leaderboard(request: Request, conn=Depends(db.get_db), kind: str = "stat",
                category: str = "", key: str = "", scope: str = "all",
                range: str = None, from_: int = timeq.ts_param("from"),
                to: int = timeq.ts_param(),
                include_bots: bool = False, limit: int = 50, offset: int = 0):
    if kind not in ("stat", "scoreboard"):
        raise ValueError(f"bad kind: {kind!r}")
    if not key or (kind == "stat" and not category):
        raise ValueError("key (and category for kind=stat) required")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = max(1, min(limit, api_settings().api.leaderboard_max_limit))
    sc = resolve_scope(conn, scope)
    now = int(time.time())
    delta = range is not None or from_ is not None
    r = timeq.parse_range(conn, sc, range, from_, to, now)

    def values_at(ts):
        if kind == "stat":
            return timeq.stat_values_at(conn, sc, include_bots, category, key, ts)
        return timeq.scoreboard_values_at(conn, sc, include_bots, key, ts)

    if delta and r.empty:
        vals = {}
    elif delta:
        end = values_at(None if r.is_now else r.to_ts)
        start = values_at(r.effective_from)
        vals = {u: end.get(u, 0) - start.get(u, 0) for u in set(end) | set(start)}
        if kind == "stat":
            # Vanilla stats only count up; a drop means the stat file was reset
            # or lost (no gain to rank), so it clamps to 0 and is left out.
            # Scoreboards can legitimately go down and keep their sign.
            vals = {u: max(v, 0) for u, v in vals.items()}
        vals = {u: v for u, v in vals.items() if v != 0}
    else:
        # total mode: values at `to` when given, else current; a 0 is no
        # standing (unused stat, reset score) and is left out like a 0 gain
        vals = {u: v for u, v in values_at(None if r.is_now else r.to_ts).items() if v != 0}
    ranked = rank_rows(vals)
    return {
        "effective_from": r.effective_from,
        "to": r.to_ts,
        "mode": "delta" if delta else "total",
        "total_count": len(ranked),
        "rows": page_rows(conn, ranked, limit, offset),
    }


def rank_rows(vals: dict) -> list:
    """{uuid: value} -> [(uuid, value, rank)], highest first (uuid breaks
    ties in order); competition ranking: ties share a rank, the next rank
    skips (1, 1, 3)."""
    ordered = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))
    out, prev, rank = [], None, 0
    for i, (u, v) in enumerate(ordered):
        if v != prev:
            rank, prev = i + 1, v
        out.append((u, v, rank))
    return out


def page_rows(conn, ranked, limit, offset) -> list:
    """One page of rank_rows output as [{rank, uuid, name, value}]."""
    page = ranked[offset:offset + limit]
    names = {}
    if page:
        marks = ",".join("?" * len(page))
        names = dict(conn.execute(
            f"SELECT uuid, name FROM players WHERE uuid IN ({marks})",
            [u for u, _, _ in page]).fetchall())
    return [{"rank": rk, "uuid": u, "name": names.get(u), "value": v} for u, v, rk in page]
