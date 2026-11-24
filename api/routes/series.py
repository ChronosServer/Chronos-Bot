import time

from fastapi import APIRouter, Depends, Request

from api import db, timeq
from api.cache import cached
from api.scope import (Scope, resolve_scope, scoreboard_filter_sql,
                       stat_filter_sql)

router = APIRouter()


def _check(kind, category, key):
    if kind not in ("stat", "scoreboard"):
        raise ValueError(f"bad kind: {kind!r}")
    if not key or (kind == "stat" and not category):
        raise ValueError("key (and category for kind=stat) required")


def _step_sum(events, from_ts, to_ts):
    """events: iterable of (at, server, value). Value at a timestamp is the sum
    over servers of each server's latest value at/before it; one point per
    distinct change timestamp, then downsampled."""
    last: dict = {}
    pts: list = []
    for at, server, value in sorted(events, key=lambda e: e[0]):
        last[server] = value
        total = sum(last.values())
        if pts and pts[-1][0] == at:
            pts[-1] = (at, total)
        else:
            pts.append((at, total))
    return [list(p) for p in timeq.downsample(pts, from_ts, to_ts)]


def _history_events(conn, scope, include_bots, kind, category, key, uuids, rng):
    """Per uuid: baseline row (latest at <= effective_from) and every change
    row in (effective_from, to_ts], per server, via the point indexes."""
    marks = ",".join("?" * len(uuids))
    if kind == "stat":
        frag, fp = stat_filter_sql(scope, include_bots, "s")
        tbl, idx, kcond, kp = ("stat_snapshots", "idx_stat_point",
                               "s.category=? AND s.stat_key=?", [category, key])
    else:
        frag, fp = scoreboard_filter_sql(scope, include_bots, "s")
        tbl, idx, kcond, kp = ("scoreboard_snapshots", "idx_scoreboard_point",
                               "s.objective=?", [key])
    base = (f"SELECT s.uuid, s.server_key, MAX(s.at), s.value FROM {tbl} s "
            f"INDEXED BY {idx} WHERE {kcond} AND s.uuid IN ({marks}) AND s.at<=? "
            f"AND {frag} GROUP BY s.server_key, s.uuid")
    rest = (f"SELECT s.uuid, s.server_key, s.at, s.value FROM {tbl} s "
            f"INDEXED BY {idx} WHERE {kcond} AND s.uuid IN ({marks}) "
            f"AND s.at>? AND s.at<=? AND {frag}")
    return (base, [*kp, *uuids, rng.effective_from, *fp],
            rest, [*kp, *uuids, rng.effective_from, rng.to_ts, *fp])


def build_series(conn, scope: Scope, include_bots: bool, kind: str,
                 category: str, key: str, uuids: list, rng) -> dict:
    out = {u: [] for u in uuids}
    if rng.empty or not uuids:
        return out
    base, bp, rest, rp = _history_events(conn, scope, include_bots, kind,
                                         category, key, uuids, rng)
    events = {u: [] for u in uuids}
    for u, server, at, value in (*conn.execute(base, bp), *conn.execute(rest, rp)):
        events[u].append((at, server, value))
    for u, ev in events.items():
        if ev:
            out[u] = _step_sum(ev, rng.effective_from, rng.to_ts)
    return out


def _parse_uuids(uuids: str) -> list:
    seen = []
    for u in (uuids or "").split(","):
        u = u.strip()
        if u and u not in seen:
            seen.append(u)
    if not 1 <= len(seen) <= 6:
        raise ValueError("uuids must contain 1 to 6 uuids")
    return seen


@router.get("/series")
@cached
def series(request: Request, conn=Depends(db.get_db), kind: str = "stat",
           category: str = "", key: str = "", uuids: str = "", scope: str = "all",
           range: str = None, from_: int = timeq.ts_param("from"),
           to: int = timeq.ts_param(),
           include_bots: bool = False):
    _check(kind, category, key)
    ulist = _parse_uuids(uuids)
    sc = resolve_scope(conn, scope)
    r = timeq.parse_range(conn, sc, range, from_, to, int(time.time()))
    return {"effective_from": r.effective_from, "to": r.to_ts,
            "series": build_series(conn, sc, include_bots, kind, category, key,
                                   ulist, r)}


def _totals_events(conn, kind, category, key, servers, col, rng):
    marks = ",".join("?" * len(servers))
    if kind == "stat":
        tbl, kcond, kp = "stat_totals", "category=? AND stat_key=?", [category, key]
    else:
        tbl, kcond, kp = "scoreboard_totals", "objective=?", [key]
    base = conn.execute(
        f"SELECT server_key, MAX(at), {col} FROM {tbl} WHERE server_key IN ({marks}) "
        f"AND {kcond} AND at<=? GROUP BY server_key",
        [*servers, *kp, rng.effective_from]).fetchall()
    rest = conn.execute(
        f"SELECT server_key, at, {col} FROM {tbl} WHERE server_key IN ({marks}) "
        f"AND {kcond} AND at>? AND at<=?",
        [*servers, *kp, rng.effective_from, rng.to_ts]).fetchall()
    out: dict = {}
    for server, at, value in (*base, *rest):
        out.setdefault(server, []).append((at, server, value))
    return out


@router.get("/totals")
@cached
def totals(request: Request, conn=Depends(db.get_db), kind: str = "stat",
           category: str = "", key: str = "", scopes: str = "all",
           range: str = None, from_: int = timeq.ts_param("from"),
           to: int = timeq.ts_param(),
           include_bots: bool = False):
    _check(kind, category, key)
    names = []
    for n in (scopes or "").split(","):
        n = n.strip()
        if n and n not in names:
            names.append(n)
    if not names:
        raise ValueError("scopes required")
    resolved = {n: resolve_scope(conn, n) for n in names}
    union = Scope("union", tuple(sorted({s for sc in resolved.values()
                                         for s in sc.servers})))
    r = timeq.parse_range(conn, union, range, from_, to, int(time.time()))
    col = "total" if include_bots else "total_no_bots"
    out = {n: [] for n in names}
    if not r.empty:
        per = _totals_events(conn, kind, category, key, union.servers, col, r)
        for n, sc in resolved.items():
            ev = [e for s in sc.servers for e in per.get(s, [])]
            if ev:
                out[n] = _step_sum(ev, r.effective_from, r.to_ts)
    return {"effective_from": r.effective_from, "to": r.to_ts, "series": out}
