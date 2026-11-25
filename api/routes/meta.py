from fastapi import APIRouter, Depends, Request

from api import db
from api.cache import cached
from api.scope import SERVER_ORDER_SQL, list_scopes, scope_group_servers
from api.settings import api_settings

router = APIRouter()


@router.get("/meta")
@cached
def get_meta(request: Request, conn=Depends(db.get_db)):
    servers = [dict(r) for r in conn.execute(
        'SELECT key, label, group_key AS "group", filters_bots, track_stats, '
        "track_scoreboards FROM servers WHERE provisioned=1 ORDER BY " + SERVER_ORDER_SQL)]
    for s in servers:
        for f in ("filters_bots", "track_stats", "track_scoreboards"):
            s[f] = bool(s[f])
    scopes = list_scopes(conn)
    m = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    def per_server(prefix):  # servers without the meta key are left out
        return {s["key"]: int(m[f"{prefix}:{s['key']}"])
                for s in servers if f"{prefix}:{s['key']}" in m}
    last = m.get("last_run_at")
    return {
        "data_version": db.data_version(conn),
        "last_run_at": int(last) if last is not None else None,
        "servers": servers,
        "scopes": scopes,
        "scope_groups": scope_group_servers(conn),
        "categories": [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM stat_current WHERE server_key IN "
            "(SELECT key FROM servers WHERE track_stats=1) ORDER BY category")],
        "tracking_started_at": per_server("tracking_started_at"),
        # first live (non-import) stats run; split point of sparse vs dense history
        "live_started_at": per_server("live_started_at"),
        "settings": api_settings().public(),
    }
