from fastapi import APIRouter, Depends, Request

from api import db
from api.cache import cached

router = APIRouter()


@router.get("/meta")
@cached
async def get_meta(request: Request, conn=Depends(db.get_db)):
    servers = [dict(r) for r in conn.execute(
        'SELECT key, label, group_key AS "group", filters_bots, track_stats, '
        "track_scoreboards FROM servers WHERE provisioned=1 ORDER BY key")]
    for s in servers:
        for f in ("filters_bots", "track_stats", "track_scoreboards"):
            s[f] = bool(s[f])
    scopes = ["all", "smp", "cmps"]
    scopes += [s["key"] for s in servers if s["key"] not in scopes]
    m = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    started = {s["key"]: int(m[f"tracking_started_at:{s['key']}"])
               for s in servers if f"tracking_started_at:{s['key']}" in m}
    last = m.get("last_run_at")
    return {
        "data_version": db.data_version(conn),
        "last_run_at": int(last) if last is not None else None,
        "servers": servers,
        "scopes": scopes,
        "categories": [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM stat_current ORDER BY category")],
        "tracking_started_at": started,
    }
