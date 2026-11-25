"""Shared participant/scope rules. The single place these live; the API and
bot/derive.py both import it.

Bot rules (include_bots=false):
- Fake players, every server: a version-3 uuid (Carpet fake players with
  made-up names get offline-mode uuids; real Mojang accounts are version 4).
- SMP team rule: servers with filters_bots=1 (config servers.<key>.filters_bots;
  default smp only) only count rows whose uuid has a player_flags(is_bot=0)
  row; a missing flag means bot.
Fragments use ? params only.

Scopes: "all", every scope_groups name (config; default "cmps" -> group
"cmp"), and every provisioned server key.
"""
import re
from dataclasses import dataclass

from api.settings import api_settings

_RESERVED = {"_scope_pf", "_scope_m", "_trk", "_mir_m", "_blk"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# Display order of servers everywhere: the order of stats_tracking.servers in
# config.json (servers.position, written by bot/snapshot.py); rows no longer in
# config (position NULL) last, by key. Use with the servers table unaliased.
SERVER_ORDER_SQL = "position IS NULL, position, key"


def order_servers(conn, keys) -> list:
    """`keys` (any iterable of server keys, deduplicated) in display order."""
    rank = {k: i for i, (k,) in enumerate(conn.execute(
        f"SELECT key FROM servers ORDER BY {SERVER_ORDER_SQL}"))}
    return sorted(set(keys), key=lambda k: (rank.get(k, len(rank)), k))


@dataclass(frozen=True)
class Scope:
    name: str
    servers: tuple


def _scope_groups(groups):
    if groups is None:
        groups = api_settings().scope_groups
    return groups


def resolve_scope(conn, name: str, groups=None) -> Scope:
    """groups: scope name -> servers.group_key (default: the API's
    settings.scope_groups)."""
    groups = _scope_groups(groups)
    prov = conn.execute(
        "SELECT key, group_key FROM servers WHERE provisioned=1 ORDER BY " + SERVER_ORDER_SQL).fetchall()
    if name == "all":
        servers = tuple(k for k, _ in prov)
    elif name in groups:
        servers = tuple(k for k, g in prov if g == groups[name])
    else:
        servers = tuple(k for k, _ in prov if k == name)
    if not servers:
        raise ValueError(f"unknown scope: {name!r}")
    return Scope(name, servers)


def list_scopes(conn, groups=None) -> list:
    """Valid scope names, in menu order: "all"; servers whose group has no
    scope name (e.g. smp); scope_groups names with at least one provisioned
    server (e.g. cmps); then the servers inside those groups."""
    groups = _scope_groups(groups)
    prov = conn.execute(
        "SELECT key, group_key FROM servers WHERE provisioned=1 ORDER BY " + SERVER_ORDER_SQL).fetchall()
    grouped = set(groups.values())
    out = ["all"]
    out += [k for k, g in prov if g not in grouped]
    out += [n for n, g in groups.items() if any(pg == g for _, pg in prov)]
    out += [k for k, g in prov if g in grouped]
    return list(dict.fromkeys(out))


def scope_group_servers(conn, groups=None) -> dict:
    """Group scope name -> its provisioned server keys, in display order, for
    every scope_groups name with at least one (the ones list_scopes offers)."""
    groups = _scope_groups(groups)
    prov = conn.execute(
        "SELECT key, group_key FROM servers WHERE provisioned=1 ORDER BY "
        + SERVER_ORDER_SQL).fetchall()
    out = {n: [k for k, pg in prov if pg == g] for n, g in groups.items()}
    return {n: ks for n, ks in out.items() if ks}


def is_fake_uuid(u) -> bool:
    """A Carpet fake player: an offline-mode (version 3) uuid."""
    return isinstance(u, str) and len(u) == 36 and u[14] == "3"


def fake_sql(col: str) -> str:
    """SQL form of is_fake_uuid for a uuid column (always false for
    'unresolved:...' participant keys and other non-uuid strings)."""
    return f"(length({col})=36 AND substr({col},15,1)='3')"


def _alias(alias):
    if not _IDENT.match(alias) or alias in _RESERVED:
        raise ValueError(f"bad alias: {alias!r}")
    return alias


def _scope_sql(scope, include_bots, a, uuid_col, blocked=True):
    marks = ",".join("?" * len(scope.servers)) or "NULL"
    sql = f"{a}.server_key IN ({marks})"
    params = list(scope.servers)
    if blocked:  # slur names: hidden always, bots on or off
        sql += (f" AND NOT EXISTS (SELECT 1 FROM blocked_players _blk "
                f"WHERE _blk.uuid={a}.{uuid_col})")
    if not include_bots:
        sql += f" AND NOT {fake_sql(f'{a}.{uuid_col}')}"
        sql += (
            f" AND ({a}.server_key NOT IN (SELECT key FROM servers WHERE filters_bots=1)"
            f" OR EXISTS (SELECT 1 FROM player_flags _scope_pf WHERE _scope_pf.server_key={a}.server_key"
            f" AND _scope_pf.uuid={a}.{uuid_col} AND _scope_pf.is_bot=0))")
    return sql, params


_TRACK = {"stats": "_trk.track_stats", "scoreboards": "_trk.track_scoreboards"}


def tracked_sql(alias: str, track) -> str:
    """" AND <fragment>" hiding rows of a server whose tracking toggle is off
    (config servers.<key>.track_stats / track_scoreboards), or "" for
    track=None. Read-time only: the rows stay, so turning the toggle back on
    shows them again. track: "stats", "scoreboards", or "category" (rows with
    a category column: category 'scoreboard' follows track_scoreboards, every
    other category track_stats -- firsts, milestones, rivalries, spotlight).
    The API hides; derive passes track=None and keeps computing on the frozen
    rows, so totals history gets no fake drop to 0."""
    if track is None:
        return ""
    a = _alias(alias)
    if track == "category":
        col = (f"CASE WHEN {a}.category = 'scoreboard' THEN _trk.track_scoreboards "
               "ELSE _trk.track_stats END")
    elif track in _TRACK:
        col = _TRACK[track]
    else:
        raise ValueError(f"bad track: {track!r}")
    return f" AND EXISTS (SELECT 1 FROM servers _trk WHERE _trk.key={a}.server_key AND {col}=1)"


def stat_filter_sql(scope: Scope, include_bots: bool, alias: str, track="stats",
                    blocked=True):
    """track: see tracked_sql ("category" for firsts/milestones/rivalries/
    spotlight rows; None in derive). blocked=False keeps blocked players
    (slur names): only derive's whole-server totals."""
    a = _alias(alias)
    sql, params = _scope_sql(scope, include_bots, a, "uuid", blocked)
    return f"({sql}{tracked_sql(a, track)})", params


def scoreboard_filter_sql(scope: Scope, include_bots: bool, alias: str,
                          is_current: bool = False, track="scoreboards", blocked=True):
    a = _alias(alias)
    if is_current:
        sql, params = _scope_sql(scope, include_bots, a, "participant_key", blocked)
        unresolved = f"{a}.participant_key NOT LIKE 'unresolved:%'"
    else:
        sql, params = _scope_sql(scope, include_bots, a, "uuid", blocked)
        unresolved = f"{a}.participant_kind != 'unresolved'"
    mirror = (f"NOT EXISTS (SELECT 1 FROM scoreboard_meta _scope_m WHERE _scope_m.server_key={a}.server_key"
              f" AND _scope_m.objective={a}.objective AND _scope_m.is_vanilla_mirror=1)")
    return f"({sql} AND {unresolved} AND {mirror}{tracked_sql(a, track)})", params


def achievement_not_mirror_sql(alias: str) -> str:
    """WHERE fragment for firsts/milestones rows (category, stat_key): drops
    scoreboard rows (category 'scoreboard', stat_key = objective) whose
    objective is now a vanilla mirror on that server. An objective created
    before its version's dictionary was bundled gets derived rows while it
    looks real; once the dictionary flags it, they are hidden (not deleted,
    like its scoreboard history)."""
    a = _alias(alias)
    return (f"NOT ({a}.category = 'scoreboard' AND EXISTS (SELECT 1 FROM scoreboard_meta _mir_m"
            f" WHERE _mir_m.server_key={a}.server_key AND _mir_m.objective={a}.stat_key"
            f" AND _mir_m.is_vanilla_mirror=1))")


def objective_not_mirror_sql(alias: str) -> str:
    """WHERE fragment for rows with server_key + objective (scoreboard_totals):
    drops objectives that are vanilla mirrors on that server."""
    a = _alias(alias)
    return (f"NOT EXISTS (SELECT 1 FROM scoreboard_meta _mir_m WHERE _mir_m.server_key="
            f"{a}.server_key AND _mir_m.objective={a}.objective AND _mir_m.is_vanilla_mirror=1)")


def is_blocked(conn, uuid) -> bool:
    """A slur name (blocked_players): hidden from the whole API."""
    return conn.execute("SELECT 1 FROM blocked_players WHERE uuid=?",
                        (uuid,)).fetchone() is not None


def is_counted(conn, server_key: str, uuid: str, include_bots: bool = False) -> bool:
    if include_bots:
        return True
    if is_fake_uuid(uuid):
        return False
    row = conn.execute("SELECT filters_bots FROM servers WHERE key=?", (server_key,)).fetchone()
    if not row or not row[0]:
        return True
    return conn.execute(
        "SELECT 1 FROM player_flags WHERE server_key=? AND uuid=? AND is_bot=0",
        (server_key, uuid)).fetchone() is not None


def counted_uuids(conn, server_key: str):
    """Batch form of is_counted(include_bots=False) for one server, apart from
    the fake-player rule (callers also check is_fake_uuid): None when the
    server never filters bots, else the set of uuids with a
    player_flags(is_bot=0) row."""
    row = conn.execute("SELECT filters_bots FROM servers WHERE key=?", (server_key,)).fetchone()
    if not row or not row[0]:
        return None
    return {u for (u,) in conn.execute(
        "SELECT uuid FROM player_flags WHERE server_key=? AND is_bot=0", (server_key,))}
