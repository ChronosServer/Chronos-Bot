import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api import db, timeq
from api.cache import cached
from api.rivals import closing_stats, window_floor
from api.routes.series import build_series
from api.scope import (achievement_not_mirror_sql, is_blocked, is_fake_uuid, order_servers,
                       resolve_scope,
                       scoreboard_filter_sql, stat_filter_sql, tracked_sql)
from api.merges import api_merges, known_former_names
from api.settings import api_settings

router = APIRouter()


def resolve_player(conn, ident: str):
    """uuid or name -> uuid; a merged-away account (player_merges.json)
    resolves to the player it was merged into."""
    ident = (ident or "").strip()
    m = api_merges()
    if conn.execute("SELECT 1 FROM players WHERE uuid=?", (ident,)).fetchone():
        return m.target(ident)
    if ident.lower() in m.by_name:
        return m.by_name[ident.lower()]
    # a former name (player_merges.json renames) that is still this player's
    # today: a dated entry whose `until` has passed no longer claims the name
    renamed = m.rename_target(ident, int(time.time()))
    if renamed:
        return renamed
    row = conn.execute(
        "SELECT uuid FROM players WHERE name=? COLLATE NOCASE "
        "ORDER BY last_observed DESC LIMIT 1", (ident,)).fetchone()
    return m.target(row[0]) if row else None


def former_names(conn, uuid, current=None) -> list:
    """`aka`: the player's former names (player_merges.json renames and
    merged accounts' names, plus the merged accounts' own player names),
    case-insensitively deduplicated, without the current name, sorted."""
    return known_former_names(conn, api_merges(), uuid, current)


def _not_found():
    return JSONResponse({"error": "player not found"}, status_code=404)


# Rank = 1 + other counted players with a strictly greater scope-summed value
# (competition ranking); `of` = other counted players + the player. Only the
# player's own keys are ranked, seeking idx_*_current_* once per key.
# A scope-summed value of 0 is no standing (an unused stat, junk keys such as
# crafted/minecraft:air, a reset score): it is neither listed nor counted in `of`.
_RANK = """
WITH mine AS (
  SELECT s.{kc} AS c, {ks} AS k, SUM(s.value) AS v FROM {tbl} s
  WHERE s.{uc}=? AND {own} GROUP BY 1, 2 HAVING SUM(s.value) != 0),
per AS (
  SELECT s.{kc} AS c, {ks} AS k, s.{uc} AS u, SUM(s.value) AS v
  FROM mine m CROSS JOIN {tbl} s INDEXED BY {idx}
  WHERE s.{kc}=m.c{kjoin} AND s.{uc}!=? AND {frag}
  GROUP BY 1, 2, 3 HAVING SUM(s.value) != 0)
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


def _rivals(conn, sc, include_bots, uuid):
    # The scope/bot rule is re-applied to the other participant at read time
    # (as /rivalries does): bot flags can change after the derive ran.
    frag, fp = stat_filter_sql(sc, include_bots, "o")
    rows = conn.execute(
        f"WITH o AS (SELECT rowid AS rid, server_key, "
        f"CASE WHEN uuid_a=? THEN uuid_b ELSE uuid_a END AS uuid FROM rivalries "
        f"WHERE uuid_a=? OR uuid_b=?) "
        f"SELECT r.server_key, r.category, r.stat_key, r.uuid_a, r.uuid_b, r.gap, "
        f"r.gap_7d_ago, r.computed_at, p.name FROM o JOIN rivalries r ON r.rowid=o.rid "
        f"LEFT JOIN players p ON p.uuid = o.uuid WHERE {frag} "
        f"ORDER BY r.server_key, r.category, r.stat_key",
        [uuid, uuid, uuid, *fp]).fetchall()
    starts, points = {}, {}
    out = []
    for sk, cat, key, a, b, gap, g7, at, oname in rows:
        if sk not in starts:
            m = conn.execute("SELECT value FROM meta WHERE key=?",
                             (f"tracking_started_at:{sk}",)).fetchone()
            starts[sk] = int(m[0]) if m and m[0] is not None else None
        floor = window_floor(conn, sk, at, starts[sk], points)
        closing, overtake = closing_stats(gap, g7, at, floor)
        out.append({"server_key": sk, "category": cat, "stat_key": key,
                    "uuid_a": a, "uuid_b": b,
                    "other_uuid": b if a == uuid else a, "other_name": oname,
                    "gap": gap, "gap_7d_ago": g7, "computed_at": at,
                    "closing_per_day": closing, "overtake_days": overtake})
    return out


def _has_rows(conn, uuid):
    """Any stat_current/scoreboard_current row on a provisioned server, scope
    and bot status aside. A players row alone (e.g. a Mojang-resolved team
    member who never joined) is not a profile."""
    return conn.execute(
        "SELECT 1 FROM servers s WHERE s.provisioned=1 AND ("
        "EXISTS (SELECT 1 FROM stat_current c WHERE c.server_key=s.key AND c.uuid=?) OR "
        "EXISTS (SELECT 1 FROM scoreboard_current c WHERE c.server_key=s.key "
        "AND c.participant_key=?)) LIMIT 1", (uuid, uuid)).fetchone() is not None


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


def bot_flags(conn, uuid) -> dict:
    """is_bot_on_smp (team rule), is_fake (version-3 uuid), is_bot (either)."""
    smp, fake = _is_bot_on_smp(conn, uuid), is_fake_uuid(uuid)
    return {"is_bot_on_smp": smp, "is_fake": fake, "is_bot": smp or fake}


@router.get("/player/{ident}")
@cached
def player(ident: str, request: Request, conn=Depends(db.get_db),
           scope: str = "all", include_bots: bool = False):
    sc = resolve_scope(conn, scope)
    uuid = resolve_player(conn, ident)
    if uuid is None or is_blocked(conn, uuid) or not _has_rows(conn, uuid):
        return _not_found()
    name, resolved, last = conn.execute(
        "SELECT name, name_resolved, last_activity FROM players WHERE uuid=?",
        (uuid,)).fetchone()
    stats = _ranked_stats(conn, sc, include_bots, uuid)
    boards = _ranked_scoreboards(conn, sc, include_bots, uuid)
    marks = ",".join("?" * len(sc.servers))
    sp = [uuid, *sc.servers]
    servers = order_servers(conn, (r[0] for r in conn.execute(
        f"SELECT DISTINCT server_key FROM stat_current t WHERE uuid=? AND server_key IN ({marks})"
        f"{tracked_sql('t', 'stats')} UNION SELECT DISTINCT server_key FROM scoreboard_current t "
        f"WHERE participant_key=? AND server_key IN ({marks}){tracked_sql('t', 'scoreboards')}",
        [*sp, *sp])))
    badges = [dict(zip(("badge_key", "server_key", "awarded_at"), r)) for r in conn.execute(
        f"SELECT badge_key, server_key, awarded_at FROM badges WHERE uuid=? AND "
        f"(server_key IS NULL OR server_key IN ({marks})) ORDER BY awarded_at, badge_key",
        sp)]
    mcols = ("at", "server_key", "rule_key", "category", "stat_key", "threshold",
             "value_at", "earliest_known", "approximate")
    milestones = [dict(zip(mcols, r)) for r in conn.execute(
        f"SELECT {', '.join(mcols)} FROM milestones WHERE uuid=? AND server_key IN ({marks}) "
        f"AND {achievement_not_mirror_sql('milestones')}{tracked_sql('milestones', 'category')} "
        f"ORDER BY at DESC, id DESC LIMIT ?",
        [*sp, api_settings().api.player_milestones_limit])]
    fcols = ("at", "server_key", "category", "stat_key", "earliest_known", "approximate")
    firsts = [dict(zip(fcols, r)) for r in conn.execute(
        f"SELECT {', '.join(fcols)} FROM firsts WHERE uuid=? AND server_key IN ({marks}) "
        f"AND {achievement_not_mirror_sql('firsts')}{tracked_sql('firsts', 'category')} "
        f"ORDER BY at, id", sp)]
    return {"uuid": uuid, "name": name, "aka": former_names(conn, uuid, name),
            "name_resolved": resolved,
            "last_activity": last, **bot_flags(conn, uuid),
            "servers": servers, "stats": stats, "scoreboards": boards,
            "badges": badges, "milestones": milestones, "firsts": firsts,
            "rivals": _rivals(conn, sc, include_bots, uuid)}


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
            from_: int = timeq.ts_param("from"), to: int = timeq.ts_param(),
            include_bots: bool = False):
    api = api_settings().api
    lo, hi = api.compare_min_players, api.compare_max_players
    players_msg = f"uuids must contain {lo} to {hi} distinct players"
    idents = list(dict.fromkeys(u.strip() for u in (uuids or "").split(",") if u.strip()))
    if not lo <= len(idents) <= hi:
        raise ValueError(players_msg)
    klist = _parse_keys(keys)
    if len(klist) > api.compare_max_keys:
        raise ValueError(f"at most {api.compare_max_keys} keys")
    sc = resolve_scope(conn, scope)
    resolved = [resolve_player(conn, i) for i in idents]
    if None in resolved or any(is_blocked(conn, u) for u in resolved):
        return _not_found()
    ulist = list(dict.fromkeys(resolved))
    if len(ulist) < lo:
        raise ValueError(players_msg)
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
            if kind == "stat":  # stats only count up: a drop is a reset (see /leaderboard)
                values[label] = {u: max(v, 0) for u, v in values[label].items()}
        else:
            cur = at(None if r.is_now else r.to_ts)
            values[label] = {u: cur.get(u, 0) for u in ulist}
        series[label] = build_series(conn, sc, include_bots, kind, cat, key, ulist, r)
    return {"players": [{"uuid": u, "name": names.get(u), **bot_flags(conn, u)}
                        for u in ulist],
            "effective_from": r.effective_from, "to": r.to_ts,
            "values": values, "series": series}
