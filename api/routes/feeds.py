import json
import random
import time

from fastapi import APIRouter, Depends, Request

from api import db, timeq
from api.cache import cached
from api.rivals import closing_stats, window_floor
from api.scope import resolve_scope, stat_filter_sql
from api.settings import api_settings

router = APIRouter()
_rng = random.SystemRandom()


def _limit(limit):
    return max(1, min(int(limit), api_settings().api.feed_max_limit))


def _parse_cursor(before):
    try:
        ek, at, id_ = (int(x) for x in before.split(":"))
    except (ValueError, AttributeError):
        raise ValueError(f"bad cursor: {before!r}")
    if ek not in (0, 1):
        raise ValueError(f"bad cursor: {before!r}")
    return ek, at, id_


def milestones_sql(frag, cur_sql=""):
    # Pinned: walks idx_milestones_feed in ORDER BY order and stops at LIMIT,
    # instead of sorting every milestone row in a temp b-tree per page.
    return (
        f"SELECT m.id, m.at, m.server_key, m.uuid, p.name, m.category, m.stat_key, "
        f"m.threshold, m.earliest_known, m.approximate FROM milestones m INDEXED BY idx_milestones_feed "
        f"LEFT JOIN players p ON p.uuid = m.uuid WHERE {frag}{cur_sql} "
        f"ORDER BY m.earliest_known ASC, m.at DESC, m.id DESC LIMIT ?")


@router.get("/milestones")
@cached
def milestones(request: Request, conn=Depends(db.get_db), scope: str = "all",
               limit: int = 50, before: str = None, include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    limit = _limit(limit)
    frag, params = stat_filter_sql(sc, include_bots, "m")
    cur_sql, cur = "", []
    if before:
        ek, at, id_ = _parse_cursor(before)
        cur_sql = (" AND (m.earliest_known > ? OR (m.earliest_known = ? AND "
                   "(m.at < ? OR (m.at = ? AND m.id < ?))))")
        cur = [ek, ek, at, at, id_]
    rows = conn.execute(milestones_sql(frag, cur_sql),
                        [*params, *cur, limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    cols = ("id", "at", "server_key", "uuid", "name", "category", "key", "threshold",
            "earliest_known", "approximate")
    out = [dict(zip(cols, r)) for r in rows]
    nxt = None
    if more and out:
        last = out[-1]
        nxt = f"{last['earliest_known']}:{last['at']}:{last['id']}"
    return {"rows": out, "next": nxt}


@router.get("/firsts")
@cached
def firsts(request: Request, conn=Depends(db.get_db), scope: str = "all",
           include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    frag, params = stat_filter_sql(sc, include_bots, "f")
    rows = conn.execute(
        f"SELECT f.server_key, f.category, f.stat_key, f.uuid, p.name, f.at, "
        f"f.earliest_known, f.approximate FROM firsts f LEFT JOIN players p ON p.uuid = f.uuid "
        f"WHERE {frag} ORDER BY f.at, f.id", params).fetchall()
    cols = ("server_key", "category", "key", "uuid", "name", "at", "earliest_known",
            "approximate")
    return {"rows": [dict(zip(cols, r)) for r in rows]}


@router.get("/rivalries")
@cached
def rivalries(request: Request, conn=Depends(db.get_db), scope: str = "all",
              uuid: str = None, limit: int = 50, include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    limit = _limit(limit)
    fa, pa = stat_filter_sql(sc, include_bots, "ra")
    fb, pb = stat_filter_sql(sc, include_bots, "rb")
    usql, up = ("", [])
    if uuid:
        usql, up = " AND (r.uuid_a=? OR r.uuid_b=?)", [uuid, uuid]
    rows = conn.execute(
        f"WITH ra AS (SELECT rowid AS rid, server_key, uuid_a AS uuid FROM rivalries), "
        f"rb AS (SELECT rowid AS rid, server_key, uuid_b AS uuid FROM rivalries) "
        f"SELECT r.server_key, r.category, r.stat_key, r.uuid_a, pa.name, r.uuid_b, "
        f"pb.name, r.gap, r.gap_7d_ago, r.computed_at FROM rivalries r "
        f"JOIN ra ON ra.rid=r.rowid JOIN rb ON rb.rid=r.rowid "
        f"LEFT JOIN players pa ON pa.uuid=r.uuid_a LEFT JOIN players pb ON pb.uuid=r.uuid_b "
        f"WHERE {fa} AND {fb}{usql}", [*pa, *pb, *up]).fetchall()
    starts, points = {}, {}
    out = []
    for sk, cat, key, a, an, b, bn, gap, g7, at in rows:
        if sk not in starts:
            m = conn.execute("SELECT value FROM meta WHERE key=?",
                             (f"tracking_started_at:{sk}",)).fetchone()
            starts[sk] = int(m[0]) if m and m[0] is not None else None
        floor = window_floor(conn, sk, at, starts[sk], points)
        closing, overtake = closing_stats(gap, g7, at, floor)
        out.append({"server_key": sk, "category": cat, "key": key,
                    "a": {"uuid": a, "name": an}, "b": {"uuid": b, "name": bn},
                    "gap": gap, "gap_7d_ago": g7, "closing_per_day": closing,
                    "overtake_days": overtake})
    out.sort(key=lambda r: (r["overtake_days"] is None,
                            r["overtake_days"] or 0, r["gap"]))
    return {"rows": out[:limit]}


@router.get("/activity")
@cached
def activity(request: Request, conn=Depends(db.get_db), scope: str = "all",
             range: str = "1m", include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    bucket = int(api_settings().api.activity_bucket_s)
    r = timeq.parse_range(conn, sc, range, None, None, int(time.time()))
    if r.empty:
        return {"bucket_seconds": bucket, "buckets": []}
    frag, params = stat_filter_sql(sc, include_bots, "a")
    rows = conn.execute(
        f"SELECT a.at / {bucket} * {bucket} AS b, COUNT(DISTINCT a.uuid) FROM activity a "
        f"WHERE a.at >= ? AND a.at <= ? AND {frag} GROUP BY b ORDER BY b",
        [r.effective_from, r.to_ts, *params]).fetchall()
    return {"bucket_seconds": bucket, "buckets": [[b, n] for b, n in rows]}


def _seen_pairs(seen):
    out = set()
    for tok in (seen or "").split(","):
        cat, sep, key = tok.strip().partition("|")
        if sep:
            out.add((cat, key))
    return out


# Deliberately not @cached: no ETag, the pick must change on every reload.
@router.get("/spotlight")
def spotlight(request: Request, conn=Depends(db.get_db), scope: str = "all",
              seen: str = "", include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    frag, params = stat_filter_sql(sc, include_bots, "c")
    rows = conn.execute(
        f"SELECT c.trigger, c.server_key, c.uuid, p.name, c.category, c.stat_key, "
        f"c.value, c.detail_json FROM spotlight_candidates c "
        f"LEFT JOIN players p ON p.uuid=c.uuid WHERE {frag}", params).fetchall()
    skip = _seen_pairs(seen)
    by_trigger: dict = {}
    for row in rows:
        if (row[4], row[5]) not in skip:
            by_trigger.setdefault(row[0], []).append(row)
    if not by_trigger:
        return {"trigger": None}
    trig = _rng.choice(sorted(by_trigger))
    t, sk, uuid, name, cat, key, value, detail = _rng.choice(by_trigger[trig])
    try:
        detail = json.loads(detail) if detail else {}
    except ValueError:
        detail = {}
    return {"trigger": t, "server_key": sk, "uuid": uuid, "name": name,
            "category": cat, "key": key, "value": value, "detail": detail}
