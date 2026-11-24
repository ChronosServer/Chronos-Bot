"""/search (answered from an in-memory index) and /keys."""
import re
import threading
from collections import defaultdict

from fastapi import APIRouter, Depends, Request

from api import db
from api.cache import cached
from api.scope import Scope, resolve_scope, scoreboard_filter_sql, stat_filter_sql

router = APIRouter()

_UUID_Q = re.compile(r"^[0-9a-f-]{8,}$")
_lock = threading.Lock()
_index = (None, None)  # (data_version, index)


def _build_index(conn):
    sc = resolve_scope(conn, "all")
    f_stat, p_stat = stat_filter_sql(sc, False, "sc")
    f_sb, p_sb = scoreboard_filter_sql(sc, False, "cur", is_current=True)
    counted = {r[0] for r in conn.execute(
        f"SELECT DISTINCT sc.uuid FROM stat_current sc WHERE {f_stat}", p_stat)}
    counted |= {r[0] for r in conn.execute(
        f"SELECT DISTINCT cur.participant_key FROM scoreboard_current cur WHERE {f_sb}", p_sb)}
    players = []
    for uuid, name in conn.execute("SELECT uuid, name FROM players"):
        if uuid in counted:
            players.append((uuid, name, (name or "").lower()))
    stats = [(c, k, k.lower()) for c, k in conn.execute(
        f"SELECT DISTINCT sc.category, sc.stat_key FROM stat_current sc WHERE {f_stat}", p_stat)]
    servers = defaultdict(list)
    for obj, srv in conn.execute(
            "SELECT m.objective, m.server_key FROM scoreboard_meta m "
            "JOIN servers s ON s.key=m.server_key AND s.provisioned=1 "
            "WHERE m.is_vanilla_mirror=0 AND m.removed=0 ORDER BY m.objective, m.server_key"):
        servers[obj].append(srv)
    objectives = [(o, s, o.lower()) for o, s in servers.items()]
    return {"players": players, "stats": stats, "objectives": objectives}


def _get_index(conn):
    global _index
    version = db.data_version(conn)
    cur = _index
    if cur[0] == version:
        return cur[1]
    with _lock:
        if _index[0] != version:
            _index = (version, _build_index(conn))
        return _index[1]


def _rank(q, texts):
    """Sort key for the best of `texts`, or None if nothing matches."""
    best = None
    for t in texts:
        i = t.find(q)
        if i < 0:
            continue
        cand = (0 if i == 0 else 1, len(t))
        if best is None or cand < best:
            best = cand
    return best


def _top(items, texts_of, q, limit, tiebreak):
    scored = []
    for it in items:
        r = _rank(q, texts_of(it))
        if r is not None:
            scored.append((r, tiebreak(it), it))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _, _, it in scored[:limit]]


@router.get("/search")
def search(request: Request, conn=Depends(db.get_db), q: str = "", limit: int = 10):
    limit = max(1, min(limit, 50))
    q = q.strip().lower()
    if not q:
        return {"players": [], "stats": [], "objectives": []}
    idx = _get_index(conn)
    by_uuid = bool(_UUID_Q.match(q))

    def ptexts(p):
        t = [p[2]] if p[1] is not None else []
        return t + ([p[0]] if by_uuid else [])
    players = _top(idx["players"], ptexts, q, limit, lambda p: (p[2], p[0]))
    stats = _top(idx["stats"],
                 lambda s: (s[2], s[2].removeprefix("minecraft:")), q, limit,
                 lambda s: (s[2], s[0]))
    objs = _top(idx["objectives"], lambda o: (o[2],), q, limit, lambda o: o[2])
    return {
        "players": [{"uuid": p[0], "name": p[1]} for p in players],
        "stats": [{"category": s[0], "key": s[1]} for s in stats],
        "objectives": [{"objective": o[0], "servers": o[1]} for o in objs],
    }


@router.get("/keys")
@cached
def keys(request: Request, conn=Depends(db.get_db), scope: str = "all",
         kind: str = "stat", include_bots: bool = False):
    if kind not in ("stat", "scoreboard"):
        raise ValueError(f"bad kind: {kind!r}")
    sc: Scope = resolve_scope(conn, scope)
    if kind == "stat":
        f, p = stat_filter_sql(sc, include_bots, "sc")
        out = defaultdict(list)
        for c, k in conn.execute(
                f"SELECT DISTINCT sc.category, sc.stat_key FROM stat_current sc "
                f"WHERE {f} ORDER BY 1, 2", p):
            out[c].append(k)
        return {"stats": dict(out)}
    marks = ",".join("?" * len(sc.servers))
    rows = conn.execute(
        f"SELECT objective, MIN(removed) FROM scoreboard_meta "
        f"WHERE is_vanilla_mirror=0 AND server_key IN ({marks}) "
        f"GROUP BY objective ORDER BY objective", list(sc.servers)).fetchall()
    return {"objectives": [{"objective": o, "removed": bool(r)} for o, r in rows]}
