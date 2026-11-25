"""/rankings: players ranked by how many #1 spots, real firsts or badges they
hold. Fake players (version-3 uuids) never appear, bots on or off; SMP
team-less bots follow include_bots; blocked names never appear."""
from fastapi import APIRouter, Depends, Request

from api import db
from api.cache import cached
from api.routes.leaderboard import page_rows, rank_rows
from api.scope import (achievement_not_mirror_sql, fake_sql, resolve_scope,
                       scoreboard_filter_sql, stat_filter_sql)
from api.settings import api_settings

router = APIRouter()

BY = ("top1", "firsts", "badges")

# #1 of a key: highest scope-summed current value > 0 (ties: all of them),
# like the top1:* (Champion) badges, but over the scope and objectives too.
_TOP1 = """
WITH v AS (
  SELECT {key} AS k, {who} AS u, SUM(s.value) AS v FROM {tbl} s
  WHERE {frag} AND NOT {fake} GROUP BY 1, 2 HAVING SUM(s.value) > 0),
r AS (SELECT u, RANK() OVER (PARTITION BY k ORDER BY v DESC) AS rk FROM v)
SELECT u, COUNT(*) FROM r WHERE rk = 1 GROUP BY u
"""


def _top1(conn, sc, include_bots):
    out = {}
    f, p = stat_filter_sql(sc, include_bots, "s")
    for u, n in conn.execute(_TOP1.format(
            key="s.category || '|' || s.stat_key", who="s.uuid", tbl="stat_current",
            frag=f, fake=fake_sql("s.uuid")), p):
        out[u] = out.get(u, 0) + n
    f, p = scoreboard_filter_sql(sc, include_bots, "s", is_current=True)
    for u, n in conn.execute(_TOP1.format(
            key="s.objective", who="s.participant_key", tbl="scoreboard_current",
            frag=f, fake=fake_sql("s.participant_key")), p):
        out[u] = out.get(u, 0) + n
    return out


def _firsts(conn, sc, include_bots):
    # earliest_known=1 ("already had it when tracking began") is not a real
    # first; approximate=1 (dated by a backup) is, like the Firsts badges.
    f, p = stat_filter_sql(sc, include_bots, "f", track="category")
    return dict(conn.execute(
        f"SELECT f.uuid, COUNT(*) FROM firsts f WHERE {f} AND "
        f"{achievement_not_mirror_sql('f')} AND f.earliest_known = 0 "
        f"AND NOT {fake_sql('f.uuid')} GROUP BY f.uuid", p).fetchall())


def _badges(conn, sc, include_bots):
    # server badges in scope (bot rule per server) plus server-less ones
    f, p = stat_filter_sql(sc, include_bots, "b", track=None)
    return dict(conn.execute(
        f"SELECT b.uuid, COUNT(DISTINCT b.badge_key) FROM badges b WHERE "
        f"((b.server_key IS NOT NULL AND {f}) OR (b.server_key IS NULL AND NOT EXISTS "
        f"(SELECT 1 FROM blocked_players x WHERE x.uuid = b.uuid))) "
        f"AND NOT {fake_sql('b.uuid')} GROUP BY b.uuid", p).fetchall())


@router.get("/rankings")
@cached
def rankings(request: Request, conn=Depends(db.get_db), by: str = "top1",
             scope: str = "all", include_bots: bool = False, limit: int = 50,
             offset: int = 0):
    if by not in BY:
        raise ValueError(f"bad by: {by!r} (use top1, firsts or badges)")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = max(1, min(limit, api_settings().api.leaderboard_max_limit))
    sc = resolve_scope(conn, scope)
    vals = {"top1": _top1, "firsts": _firsts, "badges": _badges}[by](conn, sc, include_bots)
    ranked = rank_rows({u: v for u, v in vals.items() if v > 0})
    return {"by": by, "total_count": len(ranked), "rows": page_rows(conn, ranked, limit, offset)}
