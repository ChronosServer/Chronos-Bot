import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from api import db, timeq
from api.cache import cached
from api.rivals import closing_stats
from api.routes.series import build_series
from api.scope import resolve_scope, scoreboard_filter_sql, stat_filter_sql

router = APIRouter()
MAX_COMPARE_KEYS = 20


def resolve_player(conn, ident: str):
    ident = (ident or "").strip()
    if conn.execute("SELECT 1 FROM players WHERE uuid=?", (ident,)).fetchone():
        return ident
    row = conn.execute(
        "SELECT uuid FROM players WHERE name=? COLLATE NOCASE "
        "ORDER BY last_observed DESC LIMIT 1", (ident,)).fetchone()
    return row[0] if row else None


def _not_found():
    return JSONResponse({"error": "player not found"}, status_code=404)


# Rank = 1 + other counted players with a strictly greater scope-summed value
# (competition ranking); `of` = other counted players + the player. Only the
# player's own keys are ranked, seeking idx_*_current_* once per key.
_RANK = """
WITH mine AS (
  SELECT s.{kc} AS c, {ks} AS k, SUM(s.value) AS v FROM {tbl} s
  WHERE s.{uc}=? AND {own} GROUP BY 1, 2),
per AS (
  SELECT s.{kc} AS c, {ks} AS k, s.{uc} AS u, SUM(s.value) AS v
  FROM mine m CROSS JOIN {tbl} s INDEXED BY {idx}
  WHERE s.{kc}=m.c{kjoin} AND s.{uc}!=? AND {frag}
  GROUP BY 1, 2, 3)
SELECT m.c, m.k, m.v, 1 + COALESCE(SUM(per.v > m.v), 0), 1 + COUNT(per.u)
FROM mine m LEFT JOIN per ON per.c=m.c AND per.k=m.k
GROUP BY m.c, m.k ORDER BY m.c, m.k
"""


def _ranked_stats(conn, sc, include_bots, uuid):
    own, op = stat_filter_sql(sc, True, "s")
    frag, fp = stat_filter_sql(sc, include_bots, "s")
    sql = _RANK.format(kc="category", ks="s.stat_key", tbl="stat_current", uc="uuid",
                       own=own, idx="idx_stat_current_key", frag=frag,
                       kjoin=" AND s.stat_key=m.k")
    rows = conn.execute(sql, [uuid, *op, uuid, *fp]).fetchall()
    return [{"category": c, "key": k, "value": v, "rank": r, "of": n}
            for c, k, v, r, n in rows]


def _ranked_scoreboards(conn, sc, include_bots, uuid):
    own, op = scoreboard_filter_sql(sc, True, "s", is_current=True)
    frag, fp = scoreboard_filter_sql(sc, include_bots, "s", is_current=True)
    sql = _RANK.format(kc="objective", ks="''", tbl="scoreboard_current",
                       uc="participant_key", own=own,
                       idx="idx_scoreboard_current_obj", frag=frag, kjoin="")
    rows = conn.execute(sql, [uuid, *op, uuid, *fp]).fetchall()
    return [{"objective": c, "value": v, "rank": r, "of": n}
            for c, _, v, r, n in rows]


def _rivals(conn, sc, uuid):
    marks = ",".join("?" * len(sc.servers))
    rows = conn.execute(
        f"SELECT r.server_key, r.category, r.stat_key, r.uuid_a, r.uuid_b, r.gap, "
        f"r.gap_7d_ago, r.computed_at, p.name FROM rivalries r "
        f"LEFT JOIN players p ON p.uuid = CASE WHEN r.uuid_a=? THEN r.uuid_b ELSE r.uuid_a END "
        f"WHERE (r.uuid_a=? OR r.uuid_b=?) AND r.server_key IN ({marks}) "
        f"ORDER BY r.server_key, r.category, r.stat_key",
        [uuid, uuid, uuid, *sc.servers]).fetchall()
    starts = {}
    out = []
    for sk, cat, key, a, b, gap, g7, at, oname in rows:
        if sk not in starts:
            m = conn.execute("SELECT value FROM meta WHERE key=?",
                             (f"tracking_started_at:{sk}",)).fetchone()
            starts[sk] = int(m[0]) if m and m[0] is not None else None
        closing, overtake = closing_stats(gap, g7, at, starts[sk])
        out.append({"server_key": sk, "category": cat, "stat_key": key,
                    "uuid_a": a, "uuid_b": b,
                    "other_uuid": b if a == uuid else a, "other_name": oname,
                    "gap": gap, "gap_7d_ago": g7, "computed_at": at,
                    "closing_per_day": closing, "overtake_days": overtake})
    return out


def _is_bot_on_smp(conn, uuid):
    """Over all provisioned bot-filtering servers, regardless of scope."""
    for (k,) in conn.execute(
            "SELECT key FROM servers WHERE provisioned=1 AND filters_bots=1").fetchall():
        has = conn.execute(
            "SELECT 1 FROM stat_current WHERE server_key=? AND uuid=? LIMIT 1 ",
            (k, uuid)).fetchone() or conn.execute(
            "SELECT 1 FROM scoreboard_current WHERE server_key=? AND participant_key=? "
            "LIMIT 1", (k, uuid)).fetchone()
        flagged = conn.execute(
            "SELECT 1 FROM player_flags WHERE server_key=? AND uuid=? AND is_bot=0",
            (k, uuid)).fetchone()
        if has and not flagged:
            return True
    return False


@router.get("/player/{ident}")
@cached
def player(ident: str, request: Request, conn=Depends(db.get_db),
           scope: str = "all", include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    uuid = resolve_player(conn, ident)
    if uuid is None:
        return _not_found()
    name, resolved, last = conn.execute(
        "SELECT name, name_resolved, last_activity FROM players WHERE uuid=?",
        (uuid,)).fetchone()
    stats = _ranked_stats(conn, sc, include_bots, uuid)
    boards = _ranked_scoreboards(conn, sc, include_bots, uuid)
    marks = ",".join("?" * len(sc.servers))
    sp = [uuid, *sc.servers]
    servers = sorted({r[0] for r in conn.execute(
        f"SELECT DISTINCT server_key FROM stat_current WHERE uuid=? AND server_key IN ({marks}) "
        f"UNION SELECT DISTINCT server_key FROM scoreboard_current WHERE participant_key=? "
        f"AND server_key IN ({marks})", [*sp, *sp])})
    badges = [dict(zip(("badge_key", "server_key", "awarded_at"), r)) for r in conn.execute(
        f"SELECT badge_key, server_key, awarded_at FROM badges WHERE uuid=? AND "
        f"(server_key IS NULL OR server_key IN ({marks})) ORDER BY awarded_at, badge_key",
        sp)]
    mcols = ("at", "server_key", "rule_key", "category", "stat_key", "threshold",
             "value_at", "earliest_known")
    milestones = [dict(zip(mcols, r)) for r in conn.execute(
        f"SELECT {', '.join(mcols)} FROM milestones WHERE uuid=? AND server_key IN ({marks}) "
        f"ORDER BY at DESC, id DESC LIMIT 50", sp)]
    fcols = ("at", "server_key", "category", "stat_key", "earliest_known")
    firsts = [dict(zip(fcols, r)) for r in conn.execute(
        f"SELECT {', '.join(fcols)} FROM firsts WHERE uuid=? AND server_key IN ({marks}) "
        f"ORDER BY at, id", sp)]
    return {"uuid": uuid, "name": name, "name_resolved": resolved,
            "last_activity": last, "is_bot_on_smp": _is_bot_on_smp(conn, uuid),
            "servers": servers, "stats": stats, "scoreboards": boards,
            "badges": badges, "milestones": milestones, "firsts": firsts,
            "rivals": _rivals(conn, sc, uuid)}


def _parse_keys(keys: str):
    out = []
    for k in (keys or "").split(","):
        k = k.strip()
        if not k or k in [o[0] for o in out]:
            continue
        kind, sep, rest = k.partition("|")
        if not sep or not rest:
            raise ValueError(f"bad key: {k!r} (use category|key or scoreboard|objective)")
        out.append((k, "scoreboard", "", rest) if kind == "scoreboard"
                   else (k, "stat", kind, rest))
    if not out:
        raise ValueError("keys required")
    return out


@router.get("/compare")
@cached
def compare(request: Request, conn=Depends(db.get_db), uuids: str = "",
            keys: str = "", scope: str = "all", range: str = None,
            from_: int = Query(None, alias="from"), to: int = None,
            include_bots: bool = False):
    idents = list(dict.fromkeys(u.strip() for u in (uuids or "").split(",") if u.strip()))
    if not 2 <= len(idents) <= 6:
        raise ValueError("uuids must contain 2 to 6 distinct players")
    klist = _parse_keys(keys)
    if len(klist) > MAX_COMPARE_KEYS:
        raise ValueError(f"at most {MAX_COMPARE_KEYS} keys")
    sc = resolve_scope(conn, scope)
    resolved = [resolve_player(conn, i) for i in idents]
    if None in resolved:
        return _not_found()
    ulist = list(dict.fromkeys(resolved))
    if len(ulist) < 2:
        raise ValueError("uuids must contain 2 to 6 distinct players")
    names = dict(conn.execute(
        f"SELECT uuid, name FROM players WHERE uuid IN ({','.join('?' * len(ulist))})",
        ulist).fetchall())
    r = timeq.parse_range(conn, sc, range, from_, to, int(time.time()))
    delta = range is not None or from_ is not None
    values, series = {}, {}
    for label, kind, cat, key in klist:
        def at(ts, kind=kind, cat=cat, key=key):
            if kind == "stat":
                return timeq.stat_values_at(conn, sc, include_bots, cat, key, ts, ulist)
            return timeq.scoreboard_values_at(conn, sc, include_bots, key, ts, ulist)
        if delta and r.empty:
            values[label] = {u: 0 for u in ulist}
        elif delta:
            end, start = at(None if r.is_now else r.to_ts), at(r.effective_from)
            values[label] = {u: end.get(u, 0) - start.get(u, 0) for u in ulist}
        else:
            cur = at(None)
            values[label] = {u: cur.get(u, 0) for u in ulist}
        series[label] = build_series(conn, sc, include_bots, kind, cat, key, ulist, r)
    return {"players": [{"uuid": u, "name": names.get(u)} for u in ulist],
            "effective_from": r.effective_from, "to": r.to_ts,
            "values": values, "series": series}
