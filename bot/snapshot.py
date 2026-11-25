#!/usr/bin/env python3
"""
Chronos stats snapshot runner.

Reads player stat files + scoreboard.dat directly from each tracked world's
save directory (no RCON), diffs against the last known value per
(server, participant, stat), and writes only the rows that changed into
stat_snapshots / scoreboard_snapshots. This is a change-only history store,
not a per-interval dump -- see sql/schema.sql and CLAUDE.md for why.

Run by hand first:
    python3 bot/snapshot.py

Do not wire this into a scheduler until snapshot_runs shows ok=1 on every
row across several real runs, and a second run shows rows_written collapsing
to near-zero (proof the diffing is actually working against live data).
"""

import json
import os
import sqlite3
import sys
import time
import urllib.request
import uuid as uuidlib
from datetime import datetime
from pathlib import Path

import nbt.nbt as nbt

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sql.migrations import migrate  # noqa: E402
from api.settings import DEFAULTS, load_settings  # noqa: E402
from mojang import MojangClient, default_fetch  # noqa: E402
SCHEMA_PATH = REPO_ROOT / "sql" / "schema.sql"
CONFIG_PATH = REPO_ROOT / "config.json"
DICTIONARY_DIR = REPO_ROOT / "dictionaries"

DICTIONARY_URL_TMPL = (
    "https://raw.githubusercontent.com/ChronosServer/Chronos-Bot/"
    "refs/heads/master/dictionaries/dictionary-{version}.json"
)


# --------------------------------------------------------------------------
# Config / setup
# --------------------------------------------------------------------------

def load_config(path=None):
    with open(CONFIG_PATH if path is None else path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "stats_tracking" not in cfg:
        raise SystemExit(
            "config.json has no 'stats_tracking' block. "
            "Add it before running snapshot.py -- see CLAUDE.md for the shape."
        )
    return cfg


def server_paths(cfg, server_key):
    """Returns (world_dir, world_name) for a server key, or (None, None)
    if the keys aren't present in config.json's server.* block.

    '{key}_path' is the server's root directory (contains the world folder
    plus usercache.json etc), and '{key}_world_name' is the actual save
    folder inside it (e.g. 'world-smp0'). world_dir here is the joined,
    real path that stats/ and data/scoreboard.dat live under -- callers
    must never use the raw '{key}_path' on its own."""
    server_cfg = cfg.get("server", {})
    root = server_cfg.get(f"{server_key}_path")
    name = server_cfg.get(f"{server_key}_world_name")
    if root is None or name is None:
        return None, name
    world_dir = str(Path(root) / name)
    return world_dir, name


def is_provisioned(world_path):
    """A world dir that doesn't exist yet isn't an error -- some worlds
    (snapshot, building2 at time of writing) are configured but not created."""
    return world_path is not None and Path(world_path).is_dir()


def connect_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    migrate(conn)
    return conn


def sync_servers_table(conn, cfg, now=None, settings=None):
    now = int(time.time()) if now is None else now
    settings = settings or load_settings(cfg["stats_tracking"])
    # Display order (api.scope.SERVER_ORDER_SQL) is the config's order; a row
    # no longer in config keeps NULL and sorts last, by key.
    conn.execute("UPDATE servers SET position=NULL")
    for position, (key, s) in enumerate(settings.servers.items()):
        world_path, _ = server_paths(cfg, key)
        provisioned = 1 if is_provisioned(world_path) else 0
        conn.execute(
            """
            INSERT INTO servers (key, label, group_key, filters_bots,
                                  track_stats, track_scoreboards, provisioned,
                                  position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                label=excluded.label,
                group_key=excluded.group_key,
                filters_bots=excluded.filters_bots,
                track_stats=excluded.track_stats,
                track_scoreboards=excluded.track_scoreboards,
                provisioned=excluded.provisioned,
                position=excluded.position
            """,
            (
                key,
                s.label,
                s.group,
                1 if s.filters_bots else 0,  # default: smp only (api/settings.py)
                1 if s.track_stats else 0,
                1 if s.track_scoreboards else 0,
                provisioned,
                position,
            ),
        )
    # Pseudo row so run-wide records (kind='derive') satisfy the FK on
    # snapshot_runs.server_key. provisioned=0 keeps it out of every scope.
    conn.execute(
        """
        INSERT INTO servers (key, label, group_key, filters_bots,
                              track_stats, track_scoreboards, provisioned)
        VALUES ('all', 'All', 'all', 0, 0, 0, 0)
        ON CONFLICT(key) DO UPDATE SET label='All', group_key='all',
            filters_bots=0, track_stats=0, track_scoreboards=0, provisioned=0
        """)
    conn.commit()


# --------------------------------------------------------------------------
# NBT helpers
# --------------------------------------------------------------------------

def nbt_list(tag_compound, key):
    """Paper omits Objectives/PlayerScores/Teams entirely when they'd hold
    no data -- it does NOT write an empty list. A missing tag here means
    'nothing here', never an error, and never 'everything reset to zero'."""
    if tag_compound is None or key not in tag_compound:
        return []
    return list(tag_compound[key].tags)


def nbt_val(tag, key, default=None):
    if tag is None or key not in tag:
        return default
    v = tag[key]
    return v.value if hasattr(v, "value") else v


# --------------------------------------------------------------------------
# Usercache / dictionary / real-player resolution
# --------------------------------------------------------------------------

def _expires(text):
    """usercache expiresOn ("2026-12-25 11:52:56 +0000") -> unix s; 0 if unreadable."""
    try:
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z").timestamp())
    except (TypeError, ValueError):
        return 0


def load_usercache_entries(server_root):
    """Every (uuid, name, expires) entry of <server_root>/usercache.json.
    Minecraft keeps one entry per NAME, so a renamed player has one entry
    per name they logged in with; expiresOn (last login + 1 month) says
    which is newest. Missing/unreadable file -> []."""
    path = Path(server_root) / "usercache.json"
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for e in entries if isinstance(entries, list) else []:
        u, n = e.get("uuid"), e.get("name")
        if u and n:
            out.append((u, n, _expires(e.get("expiresOn"))))
    return out


def newest_names(entries):
    """{uuid: (name, expires)} keeping each uuid's newest entry (ties: the
    later one in the list)."""
    out = {}
    for u, n, exp in entries:
        if u not in out or exp >= out[u][1]:
            out[u] = (n, exp)
    return out


def load_usercache(server_root):
    """usercache.json sits at the server root (sibling of world dirs).
    Returns {uuid: newest name} (a renamed player's old-name entry never
    wins). Missing file -> empty dict, not an error."""
    return {u: n for u, (n, _) in newest_names(load_usercache_entries(server_root)).items()}


def fetch_dictionary(conn, version, settings=None):
    """Auto-generated truncated-name -> full-vanilla-stat-name map, built by
    the bot's own scoreboard tooling. Used ONLY to detect vanilla-mirrored
    scoreboard objectives for dedup -- never as a general name resolver.
    Cached in dictionary_cache (snapshot.dictionary_cache_hours) to avoid
    hammering GitHub every run.

    The repo's own dictionaries/dictionary-<version>.json is used first (no
    network); GitHub is only the fallback when that file is missing."""
    local = DICTIONARY_DIR / f"dictionary-{version}.json"
    if local.is_file():
        with open(local, "r", encoding="utf-8") as f:
            return json.load(f)
    tuning = (settings or DEFAULTS).snapshot
    row = conn.execute(
        "SELECT fetched_at, content_json FROM dictionary_cache WHERE version = ?",
        (version,),
    ).fetchone()
    now = int(time.time())
    if row and (now - row[0]) < tuning.dictionary_cache_hours * 3600:
        return json.loads(row[1])

    url = DICTIONARY_URL_TMPL.format(version=version)
    try:
        with urllib.request.urlopen(url, timeout=tuning.dictionary_timeout_s) as resp:
            content = resp.read().decode("utf-8")
        data = json.loads(content)
        conn.execute(
            """
            INSERT INTO dictionary_cache (version, fetched_at, content_json)
            VALUES (?, ?, ?)
            ON CONFLICT(version) DO UPDATE SET
                fetched_at=excluded.fetched_at, content_json=excluded.content_json
            """,
            (version, now, content),
        )
        conn.commit()
        return data
    except Exception as e:
        if row:
            # Fall back to stale cache rather than failing the whole run.
            print(f"  [warn] dictionary fetch failed ({e}), using stale cache")
            return json.loads(row[1])
        raise


def bundled_mirror_names(directory=None):
    """Union of the values (mirror objective names) of every bundled
    dictionaries/dictionary-*.json. A live board keeps mirrors of keys an old
    version had (m-grass_path): every version's names are mirrors."""
    names = set()
    for f in sorted(Path(directory or DICTIONARY_DIR).glob("dictionary-*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                names |= set(json.load(fh).values())
        except (OSError, ValueError, AttributeError) as e:
            print(f"  [warn] can't read {f.name} ({e}); skipped")
    return names


def mirror_names(conn, version, settings=None, dictionary=None):
    """(mirror objective names, None) or (None, error). The current
    dictionary (`dictionary`, else fetch_dictionary) plus every bundled one.
    No current dictionary: the bundled union alone (warn). Neither: no set
    at all -- the caller must skip the scoreboard step rather than store
    mirror scores as if they were real objectives."""
    bundled = bundled_mirror_names()
    if dictionary is None:
        try:
            dictionary = fetch_dictionary(conn, version, settings)
        except Exception as e:
            if not bundled:
                return None, (f"no dictionary: dictionary-{version}.json unavailable ({e}) "
                              "and no bundled dictionaries/dictionary-*.json; vanilla-mirror "
                              "objectives can't be told apart, scoreboard step skipped")
            print(f"[warn] dictionary-{version}.json unavailable ({e}); using the union "
                  "of the bundled dictionaries for vanilla-mirror detection")
            return bundled, None
    return bundled | set(dictionary.values()), None


def stat_files_dir(world_path):
    # Confirmed real layout: world_dir/players/stats (not world_dir/stats --
    # there's a 'players' segment in between, per WinSCP screenshot).
    return Path(world_path) / "players" / "stats"


def known_stat_file_uuids(world_path):
    """A stat file existing for a uuid IS proof of a real join -- Minecraft
    only creates these for accounts that actually joined. usercache alone
    under-counts real players (it lags/ages out), so this is the stronger
    signal for 'is this account real'."""
    d = stat_files_dir(world_path)
    if not d.is_dir():
        return set()
    out = set()
    for p in d.glob("*.json"):
        try:
            u = str(uuidlib.UUID(p.stem))
            out.add(u)
        except ValueError:
            continue
    return out


def upsert_player(conn, uuid_str, name, now, name_resolved):
    conn.execute(
        """
        INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            name = CASE WHEN excluded.name_resolved >= players.name_resolved
                         THEN excluded.name ELSE players.name END,
            name_resolved = MAX(players.name_resolved, excluded.name_resolved),
            last_observed = excluded.last_observed
        """,
        (uuid_str, name, 1 if name_resolved else 0, now, now),
    )


# --------------------------------------------------------------------------
# Stats (player stat files) snapshot
# --------------------------------------------------------------------------

def iter_stat_files(stats_dir):
    """Yields (uuid, parsed JSON) for every <uuid>.json in stats_dir. Files
    with a non-uuid name or unreadable/invalid JSON are skipped."""
    for f in Path(stats_dir).glob("*.json"):
        try:
            player_uuid = str(uuidlib.UUID(f.stem))
        except ValueError:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        yield player_uuid, data


def run_stats_snapshot(conn, server_key, world_path, usercache, now, names=None):
    """names: {uuid: display name} (run_once: newest across every server's
    usercache); defaults to this server's usercache."""
    names = usercache if names is None else names
    stats_dir = stat_files_dir(world_path)
    if not stats_dir.is_dir():
        return {"entries_read": 0, "rows_written": 0}

    entries_read = 0
    rows_written = 0
    pending_snapshot_rows = []
    pending_current_updates = []

    cur_rows = conn.execute(
        "SELECT uuid, category, stat_key, value FROM stat_current WHERE server_key = ?",
        (server_key,),
    ).fetchall()
    current = {(u, c, k): v for (u, c, k, v) in cur_rows}

    for player_uuid, data in iter_stat_files(stats_dir):
        entries_read += 1
        name = names.get(player_uuid) or usercache.get(player_uuid)
        upsert_player(conn, player_uuid, name, now, name_resolved=bool(name))

        stats_block = data.get("stats", {})
        for category, stat_map in stats_block.items():
            for stat_key, value in stat_map.items():
                if not isinstance(value, int):
                    continue
                key = (player_uuid, category, stat_key)
                if current.get(key) == value:
                    continue  # unchanged -- no history row
                pending_snapshot_rows.append(
                    (now, server_key, player_uuid, category, stat_key, value)
                )
                pending_current_updates.append(
                    (server_key, player_uuid, category, stat_key, value, now, now)
                )
                current[key] = value
                rows_written += 1

    if pending_snapshot_rows:
        conn.executemany(
            """
            INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            pending_snapshot_rows,
        )
    if pending_current_updates:
        conn.executemany(
            """
            INSERT INTO stat_current (server_key, uuid, category, stat_key, value, updated_at, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_key, uuid, category, stat_key) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at, seen_at=excluded.seen_at
            """,
            pending_current_updates,
        )
    conn.commit()
    return {"entries_read": entries_read, "rows_written": rows_written}


# --------------------------------------------------------------------------
# Scoreboard snapshot
# --------------------------------------------------------------------------

def _resolve_participant(raw_name, name_to_uuid, known_stat_uuids):
    """A scoreboard entry's raw_name is either a real player name or a
    fake-player NPC name. Resolution is existence-based only: does this
    name map to a known uuid (via usercache), and does that uuid have a
    stat file? Never a name-shape/regex heuristic -- fake players like
    'luigiserectcock' look exactly like valid usernames."""
    u = name_to_uuid.get(raw_name)
    if u and u in known_stat_uuids:
        return u, "player"
    if u:
        return u, "player"  # resolvable via usercache even without a stat file yet
    return None, "unresolved"


def load_scoreboard_dat(world_path):
    # Confirmed real layout: world_dir/data/minecraft/scoreboard.dat
    # (not world_dir/data/scoreboard.dat -- there's an extra 'minecraft' level).
    return read_scoreboard_nbt(Path(world_path) / "data" / "minecraft" / "scoreboard.dat")


def read_scoreboard_nbt(path):
    """The scoreboard.dat 'data' compound, or None (missing/unreadable)."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        nf = nbt.NBTFile(str(path))
        return nf.get("data")
    except Exception as e:
        print(f"  [warn] failed to parse scoreboard.dat at {path}: {e}")
        return None


def run_scoreboard_snapshot(conn, server_key, world_path, usercache, dictionary, now):
    data = load_scoreboard_dat(world_path)
    objectives_tag = nbt_list(data, "Objectives")
    scores_tag = nbt_list(data, "PlayerScores")

    objective_names = {nbt_val(o, "Name") for o in objectives_tag if nbt_val(o, "Name")}
    # dictionary: mirror objective names (mirror_names), or a single
    # {full_name: truncated_name} dictionary
    if isinstance(dictionary, dict):
        mirror_full_names = set(dictionary.values())
    else:
        mirror_full_names = set(dictionary or ())

    for obj_name in objective_names:
        is_mirror = 1 if obj_name in mirror_full_names else 0
        conn.execute(
            """
            INSERT INTO scoreboard_meta (server_key, objective, is_vanilla_mirror,
                                          first_seen, last_seen, removed)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(server_key, objective) DO UPDATE SET
                is_vanilla_mirror=excluded.is_vanilla_mirror,
                last_seen=excluded.last_seen,
                removed=0
            """,
            (server_key, obj_name, is_mirror, now, now),
        )

    # Mark objectives that existed before but vanished this run.
    known_rows = conn.execute(
        "SELECT objective FROM scoreboard_meta WHERE server_key = ? AND removed = 0",
        (server_key,),
    ).fetchall()
    for (obj,) in known_rows:
        if obj not in objective_names:
            conn.execute(
                "UPDATE scoreboard_meta SET removed = 1 WHERE server_key = ? AND objective = ?",
                (server_key, obj),
            )

    name_to_uuid = {name: u for u, name in usercache.items()}
    known_stat_uuids = known_stat_file_uuids(world_path)

    cur_rows = conn.execute(
        "SELECT participant_key, objective, value FROM scoreboard_current WHERE server_key = ?",
        (server_key,),
    ).fetchall()
    current = {(p, o): v for (p, o, v) in cur_rows}

    entries_read = 0
    rows_written = 0
    pending_snapshot_rows = []
    pending_current_updates = []
    seen = set()

    for entry in scores_tag:
        raw_name = nbt_val(entry, "Name")
        obj_name = nbt_val(entry, "Objective")
        value = nbt_val(entry, "Score")
        if raw_name is None or obj_name is None or value is None:
            continue
        entries_read += 1

        # Vanilla-mirrored objectives: tracked in scoreboard_meta only.
        # Scores already live in stat_snapshots via the stat-file path --
        # storing them again here would duplicate ~9,200 of SMP's 9,346
        # objectives for nothing.
        if obj_name in mirror_full_names:
            continue

        player_uuid, kind = _resolve_participant(raw_name, name_to_uuid, known_stat_uuids)
        participant_key = player_uuid if player_uuid else f"unresolved:{raw_name}"

        key = (participant_key, obj_name)
        seen.add(key)
        if current.get(key) == value:
            continue
        pending_snapshot_rows.append(
            (now, server_key, kind, player_uuid, raw_name, obj_name, value)
        )
        pending_current_updates.append(
            (server_key, participant_key, obj_name, value, now, now)
        )
        current[key] = value
        rows_written += 1

    # A reset score (`scoreboard players reset`, the <from> side of
    # stattransfer) is an entry gone from a PlayerScores tag that is present:
    # record it as 0, or its last value stays current forever. A missing tag
    # means nothing (Paper omission); a removed objective is
    # scoreboard_meta.removed's job, so only objectives still on the board.
    if data is not None and "PlayerScores" in data:
        for (pk, obj), old in list(current.items()):
            if ((pk, obj) in seen or old == 0 or obj not in objective_names
                    or obj in mirror_full_names):
                continue
            if pk.startswith("unresolved:"):
                kind, player_uuid, raw_name = "unresolved", None, pk[len("unresolved:"):]
            else:
                kind, player_uuid = "player", pk
                row = conn.execute(
                    "SELECT raw_name FROM scoreboard_snapshots WHERE server_key=? AND uuid=? "
                    "AND objective=? ORDER BY at DESC LIMIT 1", (server_key, pk, obj)).fetchone()
                raw_name = row[0] if row else (usercache.get(pk) or pk)
            pending_snapshot_rows.append(
                (now, server_key, kind, player_uuid, raw_name, obj, 0))
            pending_current_updates.append((server_key, pk, obj, 0, now, now))
            current[(pk, obj)] = 0
            rows_written += 1

    if pending_snapshot_rows:
        conn.executemany(
            """
            INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, objective, value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            pending_snapshot_rows,
        )
    if pending_current_updates:
        conn.executemany(
            """
            INSERT INTO scoreboard_current (server_key, participant_key, objective, value, updated_at, seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_key, participant_key, objective) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at, seen_at=excluded.seen_at
            """,
            pending_current_updates,
        )
    conn.commit()
    return {"entries_read": entries_read, "rows_written": rows_written}


# --------------------------------------------------------------------------
# Bot classification via team membership (filters_bots servers: SMP only by default)
# --------------------------------------------------------------------------

def run_team_check(conn, server_key, world_path, now, usercache, mojang=None,
                   all_names=None):
    """Runs for every server with filters_bots (default: SMP only). In any
    team = real player, in no team = bot. This is classification,
    independent of whether scoreboard history is tracked -- always runs
    regardless of track_scoreboards. CMPs default to filters_bots false
    (CMPs always include everyone, by explicit decision).

    Only uuids holding a stat file on that server are classified (proof of a
    real join). Team names with no known uuid are resolved via Mojang (cached).
    all_names: {uuid: every name this server's usercache lists for it}; a
    uuid is in a team if ANY of its names is (team lists hold names, and a
    renamed player may still be listed under either)."""
    data = load_scoreboard_dat(world_path)
    teamed = set()
    for team in nbt_list(data, "Teams"):
        for player_tag in nbt_list(team, "Players"):
            name = player_tag.value if hasattr(player_tag, "value") else player_tag
            if name:
                teamed.add(str(name).lower())

    name_by_uuid = {u: n for u, n in usercache.items() if n}
    for u, n in conn.execute("SELECT uuid, name FROM players WHERE name IS NOT NULL"):
        name_by_uuid.setdefault(u, n)

    names_of = {u: {n.lower()} for u, n in name_by_uuid.items()}
    for u, ns in (all_names or {}).items():
        names_of.setdefault(u, set()).update(n.lower() for n in ns)

    if mojang is not None:
        known_names = set().union(*names_of.values()) if names_of else set()
        unknown = sorted(teamed - known_names)
        if unknown:
            for low, (resolved_uuid, display) in mojang.names_to_uuids(unknown).items():
                name_by_uuid[resolved_uuid] = display
                names_of.setdefault(resolved_uuid, set()).add(display.lower())
                upsert_player(conn, resolved_uuid, display, now, True)

    for player_uuid in known_stat_file_uuids(world_path):
        is_bot = 0 if names_of.get(player_uuid, set()) & teamed else 1
        conn.execute(
            """
            INSERT INTO player_flags (server_key, uuid, is_bot, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(server_key, uuid) DO UPDATE SET
                is_bot=excluded.is_bot, updated_at=excluded.updated_at
            """,
            (server_key, player_uuid, is_bot, now),
        )
    conn.commit()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def record_run(conn, server_key, kind, ok, error, entries_read, rows_written, duration_ms,
               now=None):
    conn.execute(
        """
        INSERT INTO snapshot_runs (at, server_key, kind, ok, error, entries_read, rows_written, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (int(time.time()) if now is None else now, server_key, kind, 1 if ok else 0, error, entries_read, rows_written, duration_ms),
    )
    conn.commit()


def run_once(config=None, now_fn=time.time, dictionary=None, mojang_fetch=None):
    cfg = config if config is not None else load_config()
    # Validated before anything is touched: a bad stats_tracking raises here.
    settings = load_settings(cfg["stats_tracking"])
    if not settings.db_path:
        raise ValueError("stats_tracking.db_path is required")
    excluded = set(settings.excluded_servers)
    version = cfg.get("server", {}).get("minecraft_version", "unknown")

    conn = connect_db(settings.db_path)
    sync_servers_table(conn, cfg, int(now_fn()), settings)
    # One run-wide timestamp: every row written this run shares `at`, so
    # derive can find the whole run with `at = run_at`.
    run_at = int(now_fn())
    if mojang_fetch is None:
        timeout = settings.mojang.timeout_s

        def mojang_fetch(method, url, body=None):
            return default_fetch(method, url, body, timeout=timeout)
    mojang = MojangClient(mojang_fetch, conn, run_at, settings=settings.mojang)

    # Every bundled version's mirror names plus the current dictionary's
    # (the same set the backup import uses). None: no dictionary at all.
    mirrors, mirror_error = mirror_names(conn, version, settings, dictionary)

    # usercache.json is per-server, sitting at the server's root directory
    # (sibling of the world folder) -- NOT shared across servers, and NOT
    # inside the world dir itself. Participant resolution stays per server;
    # only the DISPLAY name (players.name) is the newest one across every
    # server's usercache, so a server the player hasn't joined since a
    # rename can't put the old name back.
    entries = {}
    for key in settings.servers:
        root = cfg.get("server", {}).get(f"{key}_path")
        if key not in excluded and root:
            entries[key] = load_usercache_entries(root)
    display = {u: n for u, (n, _) in newest_names(
        [e for es in entries.values() for e in es]).items()}

    for key, s in settings.servers.items():
        if key in excluded:
            print(f"[{key}] excluded (mirror/tour-style duplicate) -- skipping")
            continue

        world_path, world_name = server_paths(cfg, key)
        if not is_provisioned(world_path):
            print(f"[{key}] not provisioned, skipping")
            continue

        usercache = {u: n for u, (n, _) in newest_names(entries.get(key, [])).items()}
        all_names = {}
        for u, n, _ in entries.get(key, []):
            all_names.setdefault(u, set()).add(n)

        now = run_at

        if s.track_stats:
            t0 = time.time()
            try:
                result = run_stats_snapshot(conn, key, world_path, usercache, now,
                                            names=display)
                record_run(conn, key, "stats", True, None,
                           result["entries_read"], result["rows_written"],
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] stats: ok, read={result['entries_read']} written={result['rows_written']}")
            except Exception as e:
                record_run(conn, key, "stats", False, str(e), None, None,
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] stats: ERROR {e}")

        if s.track_scoreboards and mirrors is None:
            record_run(conn, key, "scoreboards", False, mirror_error, None, None, 0, now=now)
            print(f"[{key}] scoreboards: ERROR {mirror_error}")
        elif s.track_scoreboards:
            t0 = time.time()
            try:
                result = run_scoreboard_snapshot(conn, key, world_path, usercache, mirrors, now)
                record_run(conn, key, "scoreboards", True, None,
                           result["entries_read"], result["rows_written"],
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] scoreboards: ok, read={result['entries_read']} written={result['rows_written']}")
            except Exception as e:
                record_run(conn, key, "scoreboards", False, str(e), None, None,
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] scoreboards: ERROR {e}")

        if s.filters_bots:
            t0 = time.time()
            try:
                run_team_check(conn, key, world_path, now, usercache, mojang,
                               all_names=all_names)
                record_run(conn, key, "team_check", True, None, None, None,
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] team_check: ok")
            except Exception as e:
                record_run(conn, key, "team_check", False, str(e), None, None,
                           int((time.time() - t0) * 1000), now=now)
                print(f"[{key}] team_check: ERROR {e}")

    t0 = time.time()
    try:
        import derive
        derive.run_derive(conn, run_at, mojang, settings)
        record_run(conn, "all", "derive", True, None, None, None,
                   int((time.time() - t0) * 1000), now=run_at)
        print("[all] derive: ok")
    except Exception as e:
        conn.rollback()
        record_run(conn, "all", "derive", False, str(e), None, None,
                   int((time.time() - t0) * 1000), now=run_at)
        print(f"[all] derive: ERROR {e}")

    conn.close()


if __name__ == "__main__":
    run_once()
