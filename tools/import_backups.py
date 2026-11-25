#!/usr/bin/env python3
"""Import old world backups into the stats DB as earlier history points.

    python3 tools/import_backups.py --server smp [--dry-run] [--no-backup]
        [--skip-unknown] [--keep-prefixed] [--dictionary FILE ...]
        [--date <world_dir>=YYYY-MM-DD ...] [--config config.json] <world_dir> ...
    python3 tools/import_backups.py --server smp --rebuild-only

Each backup world folder is read the way the live snapshot reads a world
(same stat-file and scoreboard.dat readers, same participant rules), its
stat keys are mapped through tools/stat_renames.json, and change-only history
rows are written at the backup's date (level.dat Data.LastPlayed, or --date).
stat_current / scoreboard_current are never written: they belong to the live
snapshot. Stop chronos-stats-snapshot.timer and .service before running.
After a real import, derive_achievements.rebuild_from_points re-derives
firsts, milestones, totals and badges over the imported points.
Operator guide: tools/README.md.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "bot")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nbt.nbt as nbt  # noqa: E402

import snapshot  # noqa: E402
from api.settings import load_settings  # noqa: E402
from derive_common import (  # noqa: E402,F401  (re-exported for callers/tests)
    BUSY_MESSAGE, BUSY_TIMEOUT_S, DatabaseBusy, begin_immediate,
)

RENAMES_PATH = Path(__file__).resolve().parent / "stat_renames.json"
DEFAULT_CONFIG = REPO_ROOT / "config.json"
# Objective-name shape of every-scoreboard's vanilla mirrors (m-stone, kb-zombie,
# z-play_time, ...). Applied to OBJECTIVE names only, never to participants,
# and only here: old backups hold mirrors of keys missing from today's dictionary.
MIRROR_PREFIX = re.compile(r"^(m|u|c|b|p|d|k|kb|z)-")
PREFIX_LIST_MAX = 20

# "Latest row before this point" for every key of one server, in one pass of
# the point index. Only imported rows exist before the first live run, so
# at < point is the same as "at <= the previous imported point".
PREV_STATS_SQL = (
    "SELECT category, stat_key, uuid, value, MAX(at) "
    "FROM stat_snapshots INDEXED BY idx_stat_point "
    "WHERE server_key=? AND at<? GROUP BY category, stat_key, uuid")
PREV_SCOREBOARD_SQL = (
    "SELECT objective, uuid, raw_name, value, MAX(at) "
    "FROM scoreboard_snapshots INDEXED BY idx_scoreboard_point "
    "WHERE server_key=? AND at<? "
    "GROUP BY objective, uuid, CASE WHEN uuid IS NULL THEN raw_name END")


@dataclass
class Backup:
    path: Path
    level_name: Optional[str]
    version_name: Optional[str]
    data_version: Optional[int]
    at: int
    stats_dir: Path
    scoreboard_path: Optional[Path]
    usercache: dict  # uuid -> newest name in this backup's usercache
    layout: str = "old"
    flat_files: int = 0  # pre-1.13 stat files, skipped one by one
    # every (uuid, name, expires) entry: a renamed player has one per name
    usercache_entries: list = field(default_factory=list)


@dataclass
class ImportResult:
    path: Path
    at: int
    players: int = 0
    rows_stats: int = 0
    rows_scoreboard: int = 0
    renamed_hits: dict = field(default_factory=dict)
    unknown_keys: set = field(default_factory=set)
    skipped_reason: Optional[str] = None
    refused: bool = False  # skipped because importing it would be wrong, not a no-op
    objectives: int = 0
    mirrors_by_dictionary: int = 0
    mirrors_by_prefix: list = field(default_factory=list)
    # (scoreboard name, latest name) pairs summed into the latest name
    renamed_players: set = field(default_factory=set)
    backup: Optional[Backup] = None


# ------------------------------------------------------------------ reading

def parse_date(text):
    """YYYY-MM-DD -> unix seconds at 00:00 UTC."""
    d = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def _level_data(world):
    path = world / "level.dat"
    if not path.is_file():
        return None
    try:
        return nbt.NBTFile(str(path)).get("Data")
    except Exception:
        return None


def read_backup(world_dir, date_override=None):
    """Reads a backup world folder's metadata. Raises ValueError (with the
    reason) when the backup must be refused."""
    world = Path(world_dir)
    if not world.is_dir():
        raise ValueError(f"not a directory: {world}")
    data = _level_data(world)
    # nbt_val unwraps .value, which a compound doesn't have: index it directly.
    version = data["Version"] if data is not None and "Version" in data else None
    last_played = snapshot.nbt_val(data, "LastPlayed")
    if date_override is not None:
        at = int(date_override)
    elif last_played:
        at = int(last_played) // 1000
    else:
        raise ValueError("no readable date (level.dat Data.LastPlayed); pass --date DIR=YYYY-MM-DD")

    if (world / "players" / "stats").is_dir():
        stats_dir, layout = world / "players" / "stats", "new"
    elif (world / "stats").is_dir():
        stats_dir, layout = world / "stats", "old"
    else:
        raise ValueError("no stats/ or players/stats/ directory")
    # SMP began on 1.12: a player who never logged in after 1.13 keeps a flat
    # pre-1.13 file in every backup. Those are skipped one by one; only a
    # backup with pre-1.13 files and no 1.13+ file at all is refused (an empty
    # stats folder is not: its scoreboard can still be imported).
    modern = flat = 0
    for _, content in snapshot.iter_stat_files(stats_dir):
        if is_modern(content):
            modern += 1
        else:
            flat += 1
    if flat and not modern:
        raise ValueError(f"pre-1.13 stats format is not supported (all {flat} stat files "
                         "are pre-1.13, none is 1.13+)")

    scoreboard_path = None
    for candidate in (world / "data" / "minecraft" / "scoreboard.dat",
                      world / "data" / "scoreboard.dat"):
        if candidate.is_file():
            scoreboard_path = candidate
            break

    # usercache.json sits next to the world folder (server root); some
    # backups keep it inside the world folder instead.
    entries = (snapshot.load_usercache_entries(world.parent)
               or snapshot.load_usercache_entries(world))
    usercache = {u: n for u, (n, _) in snapshot.newest_names(entries).items()}
    return Backup(
        path=world,
        level_name=snapshot.nbt_val(data, "LevelName"),
        version_name=snapshot.nbt_val(version, "Name"),
        data_version=snapshot.nbt_val(data, "DataVersion"),
        at=at, stats_dir=stats_dir, scoreboard_path=scoreboard_path,
        usercache=usercache, layout=layout, flat_files=flat, usercache_entries=entries)


def is_modern(content):
    """1.13+ stat file ({"stats": {category: {key: value}}}); pre-1.13 files
    are flat ({"stat.mineBlock.minecraft.stone": 5, ...})."""
    return isinstance(content.get("stats"), dict)


def load_renames(path):
    """category -> {old_key: new_key}; "*" applies to every category.
    Top-level keys starting with "_" are comments."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {cat: dict(m) for cat, m in raw.items() if not cat.startswith("_")}


def merge_dictionaries(current, paths):
    """Mirror objective names: values of the current dictionary plus values
    of every extra dictionary-<version>.json ({full_name: truncated_name})."""
    names = set(current.values()) if isinstance(current, Mapping) else set(current or ())
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            names |= set(json.load(f).values())
    return names


# ------------------------------------------------------------------ helpers

def live_start(conn, server_key):
    return conn.execute(
        "SELECT MIN(at) FROM snapshot_runs WHERE server_key=? AND kind='stats' "
        "AND ok=1 AND entries_read>0", (server_key,)).fetchone()[0]


def acquire_write_lock(conn, timeout_s=BUSY_TIMEOUT_S):
    """BEGIN IMMEDIATE on conn (unless the caller already holds a
    transaction); DatabaseBusy when another writer holds the DB."""
    if conn.in_transaction:
        return
    begin_immediate(conn, timeout_s)


def _same_path(a, b):
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return str(a) == str(b)


def plan(conn, server_key, backups):
    """Processing order (oldest first; equal dates: later argument wins) and
    why each backup is skipped: [(index, backup, reason|None, refused, warn)].
    Rerun detection keys on (server_key, world_path, last_played): the same
    date, or the same world folder already imported at another date."""
    indexed = sorted(enumerate(backups), key=lambda ib: (ib[1].at, ib[0]))
    winners = {}
    for i, b in indexed:
        winners[b.at] = i
    start = live_start(conn, server_key)
    done = conn.execute("SELECT world_path, last_played FROM imports WHERE server_key=?",
                        (server_key,)).fetchall()
    done_at = {at for _, at in done}
    out = []
    for i, b in indexed:
        reason, refused, warn = None, False, False
        same_path = [at for path, at in done if path and _same_path(path, b.path)]
        if winners[b.at] != i:
            reason = f"same date as {backups[winners[b.at]].path} (later argument wins)"
            warn = True
        elif start is not None and b.at >= start:
            reason = ("dated on or after the first live snapshot "
                      f"({_fmt(start)}); it would rewrite the live timeline")
            refused = True
        elif b.at in done_at:
            other = [path for path, at in done
                     if at == b.at and path and not _same_path(path, b.path)]
            if other:
                reason = (f"a different folder ({other[0]}) was already imported with this "
                          "date; not importing")
                warn = True
            else:
                reason = "already imported"
        elif same_path:
            reason = (f"already imported (this folder, dated {_fmt(same_path[0])}); "
                      "not importing it twice")
            warn = True
        out.append((i, b, reason, refused, warn))
    return out


def backup_db(conn, db_path, now=None):
    """SQLite online backup next to the DB: pre-import-YYYYmmdd-HHMMSS.db."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() if now is None else now))
    dest_path = Path(db_path).parent / f"pre-import-{stamp}.db"
    dest = sqlite3.connect(str(dest_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return dest_path


def _map_stats(content, renames, known_keys, targets, skip_unknown, result):
    """One stat file -> {(category, key): value}, renamed keys merged (summed)."""
    out = {}
    for category, stat_map in content.get("stats", {}).items():
        if not isinstance(stat_map, dict):
            continue
        cat_renames = renames.get(category, {})
        any_renames = renames.get("*", {})
        for key, value in stat_map.items():
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            target = cat_renames.get(key) or any_renames.get(key)
            if target:
                hit = f"{category}/{key}"
                result.renamed_hits[hit] = result.renamed_hits.get(hit, 0) + 1
            else:
                target = key
                if (category, key) not in known_keys and key not in targets:
                    result.unknown_keys.add(f"{category}/{key}")
                    if skip_unknown:
                        continue
            k = (category, target)
            out[k] = out.get(k, 0) + value
    return out


def _resolve_names(conn, backups):
    """name -> uuid for scoreboard participants: players' names, overridden
    by every name in the backups' usercaches, old-name entries included (a
    renamed player has one entry per name). Oldest backup first and, within
    one, oldest entry first, so a name later used by someone else maps to
    its newest holder."""
    name_to_uuid = {n: u for u, n in conn.execute(
        "SELECT uuid, name FROM players WHERE name IS NOT NULL ORDER BY last_observed")}
    for b in backups:
        for u, n, _ in sorted(b.usercache_entries, key=lambda e: e[2]):
            name_to_uuid[n] = u
        name_to_uuid.update({n: u for u, n in b.usercache.items()})
    return name_to_uuid


def _fallback_names(conn, backups):
    """uuid -> the player's latest known name: the live name (players), else
    the newest backup's usercache name."""
    out = {}
    for b in backups:  # oldest -> newest
        out.update(b.usercache)
    out.update({u: n for u, n in conn.execute(
        "SELECT uuid, name FROM players WHERE name IS NOT NULL")})
    return out


def _player_rows(conn, uuids, at, names):
    """Missing players are inserted (first_seen = last_observed = backup date);
    existing ones get first_seen moved back to the backup date when earlier,
    and a NULL name filled. last_observed is never touched."""
    for u in uuids:
        name = names.get(u)
        conn.execute(
            "INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uuid) DO UPDATE SET "
            "first_seen = MIN(players.first_seen, excluded.first_seen), "
            "name = CASE WHEN players.name IS NULL AND excluded.name IS NOT NULL "
            " THEN excluded.name ELSE players.name END, "
            "name_resolved = CASE WHEN players.name IS NULL AND excluded.name IS NOT NULL "
            " THEN 1 ELSE players.name_resolved END",
            (u, name, 1 if name else 0, at, at))


# ------------------------------------------------------------------ importing

def import_backups(conn, server_key, backups, renames, dictionary, dry_run, *,
                   skip_unknown=False, keep_prefixed=False,
                   lock_timeout_s=BUSY_TIMEOUT_S):
    """Imports backups (any order; processed oldest first) as history points
    of server_key. Returns one ImportResult per backup, skipped ones included.
    Non-dry: takes the write lock (unless the caller already holds it), writes
    everything in one transaction and commits; DatabaseBusy if it can't."""
    srv = conn.execute("SELECT track_stats, track_scoreboards FROM servers WHERE key=?",
                       (server_key,)).fetchone()
    if srv is None:
        raise ValueError(f"server {server_key!r} is not in the DB; run bot/snapshot.py once first")
    track_stats, track_scoreboards = bool(srv[0]), bool(srv[1])

    if not dry_run:
        acquire_write_lock(conn, lock_timeout_s)
    try:
        results = _import(conn, server_key, backups, renames, dictionary, dry_run,
                          skip_unknown, keep_prefixed, track_stats, track_scoreboards)
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    if dry_run:
        if conn.in_transaction:
            conn.rollback()
    else:
        conn.commit()
    return results


def _import(conn, server_key, backups, renames, dictionary, dry_run, skip_unknown,
            keep_prefixed, track_stats, track_scoreboards):
    steps = plan(conn, server_key, backups)
    # Known = seen live on any server, or a rename target (a backup newer than
    # the rename holds the target key itself, even if no live player has it).
    known_keys = set(conn.execute("SELECT DISTINCT category, stat_key FROM stat_current"))
    targets = {t for m in renames.values() for t in m.values()}
    mirrors = merge_dictionaries(dictionary, [])
    mirrors |= {o for (o,) in conn.execute(
        "SELECT objective FROM scoreboard_meta WHERE server_key=? AND is_vanilla_mirror=1",
        (server_key,))}

    winner = {b.at: i for i, b, *_ in steps}  # steps are (date, argument) ordered
    live_backups = [b for i, b, *_ in steps if winner[b.at] == i]
    name_to_uuid = _resolve_names(conn, live_backups)
    fallback = _fallback_names(conn, live_backups)
    names = {}
    for b in live_backups:  # oldest -> newest: the newest name wins
        names.update(b.usercache)

    # In a dry run nothing reaches the DB, so this run's points are carried here.
    carried_stats, carried_sb = {}, {}
    results = []
    for _, b, reason, refused, warn in steps:
        r = ImportResult(path=b.path, at=b.at, backup=b, skipped_reason=reason,
                         refused=refused)
        results.append(r)
        if reason:
            if warn:
                print(f"[warn] {b.path}: {reason}")
            continue

        t0 = time.time()
        stat_rows, uuids = [], []
        if track_stats:
            stat_rows, uuids = _stat_rows(conn, server_key, b, renames, known_keys,
                                          targets, skip_unknown, r,
                                          carried_stats if dry_run else None)
            r.players = len(uuids)
        sb_rows = []
        if track_scoreboards:
            sb_rows = _scoreboard_rows(conn, server_key, b, mirrors, keep_prefixed,
                                       name_to_uuid, fallback, r,
                                       carried_sb if dry_run else None)
        r.rows_stats, r.rows_scoreboard = len(stat_rows), len(sb_rows)
        if dry_run:
            continue

        _player_rows(conn, uuids, b.at, names)
        conn.executemany(
            "INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
            "VALUES (?, ?, ?, ?, ?, ?)", stat_rows)
        conn.executemany(
            "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, "
            "raw_name, objective, value) VALUES (?, ?, ?, ?, ?, ?, ?)", sb_rows)
        written = r.rows_stats + r.rows_scoreboard
        conn.execute(
            "INSERT INTO snapshot_runs (at, server_key, kind, ok, error, entries_read, "
            "rows_written, duration_ms) VALUES (?, ?, 'import', 1, NULL, ?, ?, ?)",
            (b.at, server_key, r.players, written, int((time.time() - t0) * 1000)))
        conn.execute(
            "INSERT INTO imports (world_path, level_name, server_key, last_played, "
            "data_version, imported_at, rows_written) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(Path(b.path).resolve()), b.level_name, server_key, b.at, b.data_version,
             int(time.time()), written))
    return results


def reimport_scoreboards(conn, server_key, backups, mirrors, dry_run, *,
                         keep_prefixed=False, lock_timeout_s=BUSY_TIMEOUT_S):
    """Repair: replaces the scoreboard rows at already-imported points with
    rows read again from those backups under the current participant rules
    (e.g. after the rename fix). `backups` are the imported ones, dated at
    their import point. Stat rows, live rows and stat_current/scoreboard_current
    are untouched; snapshot_runs/imports rows_written are adjusted. One
    transaction; a dry run rolls back. Returns [(backup, deleted, written,
    renamed)], renamed = {(scoreboard name, latest name)} summed at that point."""
    start = live_start(conn, server_key)
    backups = sorted(backups, key=lambda b: b.at)
    for b in backups:
        if start is not None and b.at >= start:
            raise ValueError(f"{b.path}: dated on/after the first live snapshot")
    acquire_write_lock(conn, lock_timeout_s)
    out = []
    try:
        name_to_uuid = _resolve_names(conn, backups)
        fallback = _fallback_names(conn, backups)
        for b in backups:
            n = conn.execute("DELETE FROM scoreboard_snapshots WHERE server_key=? AND at=?",
                             (server_key, b.at)).rowcount
            out.append([b, n, 0, set()])
        # Oldest first, inserted as we go: each point's change-only check sees
        # the rows just written for the points before it.
        for item in out:
            b = item[0]
            r = ImportResult(path=b.path, at=b.at, backup=b)
            rows = _scoreboard_rows(conn, server_key, b, mirrors, keep_prefixed,
                                    name_to_uuid, fallback, r, None)
            conn.executemany(
                "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, "
                "raw_name, objective, value) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            item[2], item[3] = len(rows), r.renamed_players
            diff = len(rows) - item[1]
            conn.execute("UPDATE snapshot_runs SET rows_written = rows_written + ? "
                         "WHERE server_key=? AND kind='import' AND at=?", (diff, server_key, b.at))
            conn.execute("UPDATE imports SET rows_written = rows_written + ? "
                         "WHERE server_key=? AND last_played=?", (diff, server_key, b.at))
    except BaseException:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return [tuple(x) for x in out]


def imported_backups(conn, server_key):
    """The server's imported backups re-read from their recorded folders,
    dated at their import point; (backups, missing folder errors)."""
    backups, errors = [], []
    for path, at in conn.execute(
            "SELECT world_path, last_played FROM imports WHERE server_key=? "
            "ORDER BY last_played", (server_key,)):
        try:
            backups.append(read_backup(Path(path), at))
        except ValueError as e:
            errors.append(f"{path}: {e}")
    return backups, errors


def _stat_rows(conn, server_key, b, renames, known_keys, targets, skip_unknown, r,
               carried):
    prev = {(c, k, u): v for c, k, u, v, _ in
            conn.execute(PREV_STATS_SQL, (server_key, b.at))}
    if carried is not None:
        prev.update(carried)
    rows, uuids = [], []
    for u, content in snapshot.iter_stat_files(b.stats_dir):
        if not is_modern(content):
            continue  # pre-1.13 flat file (counted in b.flat_files)
        uuids.append(u)
        for (cat, key), value in _map_stats(content, renames, known_keys, targets,
                                            skip_unknown, r).items():
            if prev.get((cat, key, u), 0) == value:
                continue  # unchanged since the previous point (or 0 and never seen)
            rows.append((b.at, server_key, u, cat, key, value))
            if carried is not None:
                carried[(cat, key, u)] = value
    return rows, uuids


def _scoreboard_rows(conn, server_key, b, mirrors, keep_prefixed, name_to_uuid,
                     fallback_names, r, carried):
    data = snapshot.read_scoreboard_nbt(b.scoreboard_path) if b.scoreboard_path else None
    objectives = {snapshot.nbt_val(o, "Name") for o in snapshot.nbt_list(data, "Objectives")}
    objectives.discard(None)
    r.objectives = len(objectives)
    by_dict = {o for o in objectives if o in mirrors}
    by_prefix = set() if keep_prefixed else {
        o for o in objectives - by_dict if MIRROR_PREFIX.match(o)}
    r.mirrors_by_dictionary, r.mirrors_by_prefix = len(by_dict), sorted(by_prefix)
    skip = by_dict | by_prefix

    prev = {}
    for obj, u, raw, v, _ in conn.execute(PREV_SCOREBOARD_SQL, (server_key, b.at)):
        prev[(obj, u or f"unresolved:{raw}")] = v
    if carried is not None:
        prev.update(carried)
    stat_uuids = {u for u, _ in snapshot.iter_stat_files(b.stats_dir)}
    # key -> [kind, uuid, raw_name, value]. Every name of one player (an old
    # name left on the board after a rename, e.g. NotQuiteRat_ and NotQuiteRat)
    # is summed into one row under the player's latest name: what the in-game
    # `stattransfer <old> <new>` does live, so history has no jump at the
    # transfer and never two rows for one (uuid, objective, at).
    merged = {}
    for entry in snapshot.nbt_list(data, "PlayerScores"):
        raw = snapshot.nbt_val(entry, "Name")
        obj = snapshot.nbt_val(entry, "Objective")
        value = snapshot.nbt_val(entry, "Score")
        if raw is None or obj is None or value is None:
            continue
        if obj in skip:
            continue
        u, kind = snapshot._resolve_participant(raw, name_to_uuid, stat_uuids)
        key = (obj, u or f"unresolved:{raw}")
        if key in merged:
            merged[key][3] += value
        else:
            name = fallback_names.get(u, raw) if u else raw
            merged[key] = [kind, u, name, value]
        if u and raw != merged[key][2]:
            r.renamed_players.add((raw, merged[key][2]))
    rows = []
    for key, (kind, u, name, value) in merged.items():
        if prev.get(key, 0) == value:
            continue
        prev[key] = value
        rows.append((b.at, server_key, kind, u, name, key[0], value))
        if carried is not None:
            carried[key] = value
    return rows


# ------------------------------------------------------------------ CLI

def _fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _print_result(r, dry_run):
    b = r.backup
    print(f"{b.path}")
    print(f"  level {b.level_name!r}, version {b.version_name} (data {b.data_version}), "
          f"date {_fmt(b.at)}, layout {b.layout} ({b.stats_dir.relative_to(b.path)})")
    if r.skipped_reason:
        print(f"  skipped: {r.skipped_reason}")
        return
    verb = "would write" if dry_run else "wrote"
    print(f"  players {r.players}; {verb} {r.rows_stats} stat rows, "
          f"{r.rows_scoreboard} scoreboard rows")
    print(f"  pre-1.13 stat files skipped: {b.flat_files}")
    print(f"  scoreboard objectives {r.objectives}; mirrors skipped by dictionary: "
          f"{r.mirrors_by_dictionary}")
    if r.mirrors_by_prefix:
        first = ", ".join(r.mirrors_by_prefix[:PREFIX_LIST_MAX])
        print(f"  probable mirrors skipped: {len(r.mirrors_by_prefix)} "
              f"(first {PREFIX_LIST_MAX}: {first})")
    else:
        print("  probable mirrors skipped: 0")
    if r.renamed_hits:
        print("  renamed keys: " + ", ".join(
            f"{k} x{n}" for k, n in sorted(r.renamed_hits.items())))
    if r.unknown_keys:
        print(f"  unknown keys ({len(r.unknown_keys)}): " + ", ".join(sorted(r.unknown_keys)))
    _print_renamed(r)


def _print_renamed(r):
    if r.renamed_players:
        print(f"  old names summed into the current name ({len(r.renamed_players)}): " +
              ", ".join(f"{a} -> {b}" for a, b in sorted(r.renamed_players)))


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="import_backups.py",
        description="Import old world backups into the stats DB as earlier history points.")
    p.add_argument("--server", required=True, help="tracked server key, e.g. smp")
    p.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    p.add_argument("--no-backup", action="store_true",
                   help="skip the pre-import online backup of the DB")
    p.add_argument("--skip-unknown", action="store_true",
                   help="don't import keys unknown to the rename table and the live DB")
    p.add_argument("--keep-prefixed", action="store_true",
                   help="import m-/u-/c-/b-/p-/d-/k-/kb-/z- objectives not in a dictionary")
    p.add_argument("--dictionary", action="append", default=[], metavar="FILE",
                   help="extra dictionary-<version>.json (mirror objective names); repeatable")
    p.add_argument("--date", action="append", default=[], metavar="DIR=YYYY-MM-DD",
                   help="override a backup's date (00:00 UTC); repeatable")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="config.json path")
    p.add_argument("--rebuild-only", action="store_true",
                   help="import nothing; only rerun the derived-data rebuild for --server")
    p.add_argument("--reimport-scoreboards", action="store_true",
                   help="repair: rewrite the scoreboard rows of every already-imported "
                        "backup of --server from its recorded folder, then rebuild")
    p.add_argument("worlds", nargs="*", metavar="world_dir")
    return p.parse_args(argv)


def _load(args):
    """(config, settings) or an error message (exit 2)."""
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None, f"config file not found: {args.config} (pass --config)"
    except (OSError, ValueError) as e:
        return None, f"can't read config {args.config}: {e}"
    if not isinstance(cfg, dict) or not isinstance(cfg.get("stats_tracking"), dict):
        return None, f"{args.config} has no stats_tracking block (see CLAUDE.md)"
    try:
        settings = load_settings(cfg["stats_tracking"])
    except ValueError as e:
        return None, f"bad stats_tracking in {args.config}: {e}"
    if args.server not in settings.servers:
        return None, f"{args.server!r} is not in stats_tracking.servers"
    if not settings.db_path:
        return None, "stats_tracking.db_path is required"
    return (cfg, settings), None


def _date_overrides(args):
    """{resolved world path: unix s} or an error message (exit 2)."""
    worlds = {str(Path(w).resolve()) for w in args.worlds}
    overrides = {}
    for item in args.date:
        d, sep, day = item.rpartition("=")
        if not sep or not d:
            return None, f"--date expects DIR=YYYY-MM-DD, got {item!r}"
        try:
            at = parse_date(day)
        except ValueError:
            return None, f"--date {item!r}: {day!r} is not a valid YYYY-MM-DD date"
        key = str(Path(d).resolve())
        if key not in worlds:
            return None, (f"--date {item!r}: {d} is not among the world folders given "
                          "(spell it the same way as the folder argument)")
        overrides[key] = at
    return overrides, None


def _rebuild_only(args, settings):
    conn = snapshot.connect_db(settings.db_path)
    try:
        if conn.execute("SELECT 1 FROM servers WHERE key=?", (args.server,)).fetchone() is None:
            print(f"error: server {args.server!r} is not in the DB")
            return 2
        import derive_achievements
        t0 = time.time()
        try:
            counts = derive_achievements.rebuild_from_points(conn, args.server, settings)
        except DatabaseBusy as e:
            print(f"error: {e}")
            return 1
        print(f"rebuild: {counts} in {time.time() - t0:.1f}s")
        return 0
    finally:
        conn.close()


def _mirrors(conn, cfg, settings, args, dictionary):
    """Mirror objective names (current + every bundled dictionary + --dictionary),
    or raises (no current dictionary)."""
    if dictionary is None:
        version = cfg.get("server", {}).get("minecraft_version", "unknown")
        dictionary = snapshot.fetch_dictionary(conn, version, settings)
    # Every dictionary in the repo (1.16 .. current) plus any --dictionary files:
    # old backups hold mirrors of names that no longer exist today.
    bundled = sorted(str(f) for f in snapshot.DICTIONARY_DIR.glob("dictionary-*.json"))
    return merge_dictionaries(dictionary, bundled + list(args.dictionary))


def _reimport_scoreboards(args, cfg, settings, dictionary):
    conn = snapshot.connect_db(settings.db_path)
    try:
        backups, errors = imported_backups(conn, args.server)
        if errors:
            for e in errors:
                print(f"error: {e}")
            print("every imported backup folder must still be readable; nothing changed")
            return 1
        if not backups:
            print(f"nothing to do: {args.server} has no imported backups")
            return 0
        try:
            mirrors = _mirrors(conn, cfg, settings, args, dictionary)
        except Exception as e:
            print(f"error: current dictionary unavailable ({e})")
            return 1
        if not args.dry_run:
            try:
                acquire_write_lock(conn)
                conn.rollback()
            except DatabaseBusy as e:
                print(f"error: {e}")
                return 1
            if not args.no_backup:
                print(f"DB backup: {backup_db(conn, settings.db_path)}")
        try:
            res = reimport_scoreboards(conn, args.server, backups, mirrors, args.dry_run,
                                       keep_prefixed=args.keep_prefixed)
        except DatabaseBusy as e:
            print(f"error: {e}")
            return 1
        print(("DRY RUN, nothing written" if args.dry_run else "Rewritten") +
              f" - {args.server} scoreboard rows at import points")
        for b, deleted, written, renamed in res:
            print(f"  {_fmt(b.at)}  {b.path}: {deleted} -> {written} rows")
            _print_renamed(ImportResult(path=b.path, at=b.at, renamed_players=renamed))
        if args.dry_run:
            return 0
        import derive_achievements
        t0 = time.time()
        try:
            counts = derive_achievements.rebuild_from_points(conn, args.server, settings)
        except DatabaseBusy as e:
            print(f"error: rebuild not run: {e}. Rows are committed; run "
                  f"import_backups.py --server {args.server} --rebuild-only")
            return 1
        print(f"rebuild: {counts} in {time.time() - t0:.1f}s")
        return 0
    finally:
        conn.close()


def main(argv=None, *, dictionary=None):
    """`dictionary` (tests) replaces the fetched current dictionary.
    Exit codes: 0 ok, 1 a backup refused / DB busy / dictionary missing,
    2 bad arguments or config."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    loaded, err = _load(args)
    if err is None:
        overrides, err = _date_overrides(args)
    if err:
        print(f"error: {err}")
        return 2
    cfg, settings = loaded
    if args.rebuild_only:
        if args.dry_run or args.worlds:
            print("error: --rebuild-only takes no world folders and no --dry-run")
            return 2
        return _rebuild_only(args, settings)
    if args.reimport_scoreboards:
        if args.worlds or args.date:
            print("error: --reimport-scoreboards takes no world folders and no --date "
                  "(it uses the recorded imports)")
            return 2
        return _reimport_scoreboards(args, cfg, settings, dictionary)
    if not args.worlds:
        print("error: give at least one world folder (or --rebuild-only)")
        return 2

    backups, refused = [], 0
    for w in args.worlds:
        try:
            backups.append(read_backup(Path(w), overrides.get(str(Path(w).resolve()))))
        except ValueError as e:
            refused += 1
            print(f"{w}\n  refused: {e}")
    renames = load_renames(RENAMES_PATH)

    conn = snapshot.connect_db(settings.db_path)
    try:
        if conn.execute("SELECT 1 FROM servers WHERE key=?", (args.server,)).fetchone() is None:
            print(f"error: server {args.server!r} is not in the DB; run bot/snapshot.py once "
                  "first")
            return 2
        if live_start(conn, args.server) is None:
            print(f"[warn] {args.server} has no live snapshot yet: import after the first "
                  "live run, or its crossings will not be marked approximate")
        try:
            mirrors = _mirrors(conn, cfg, settings, args, dictionary)
        except Exception as e:
            version = cfg.get("server", {}).get("minecraft_version", "unknown")
            print(f"error: dictionary-{version}.json unavailable ({e}); without it "
                  "vanilla-mirror objectives can't be skipped")
            return 1

        pending = any(step[2] is None for step in plan(conn, args.server, backups))
        if not args.dry_run and pending:
            try:
                # Probe first so a running snapshot aborts us before a long DB
                # backup. The lock is released for the backup (an online backup
                # from a connection holding BEGIN IMMEDIATE never completes) and
                # taken again by import_backups.
                acquire_write_lock(conn)
                conn.rollback()
            except DatabaseBusy as e:
                print(f"error: {e}")
                return 1
            if not args.no_backup:
                print(f"DB backup: {backup_db(conn, settings.db_path)}")
        try:
            results = import_backups(conn, args.server, backups, renames, mirrors,
                                     args.dry_run, skip_unknown=args.skip_unknown,
                                     keep_prefixed=args.keep_prefixed)
        except DatabaseBusy as e:
            print(f"error: {e}")
            return 1

        print(("DRY RUN, nothing written" if args.dry_run else "Imported") +
              f" - server {args.server}")
        for r in results:
            _print_result(r, args.dry_run)
        imported = [r for r in results if r.skipped_reason is None]
        refused += sum(1 for r in results if r.refused)
        print(f"total: {len(imported)} {'to import' if args.dry_run else 'imported'}, "
              f"{len(results) - len(imported)} skipped, {refused} refused; "
              f"rows {sum(r.rows_stats for r in imported)} stats, "
              f"{sum(r.rows_scoreboard for r in imported)} scoreboard")

        if not args.dry_run and imported:
            import derive_achievements
            t0 = time.time()
            try:
                counts = derive_achievements.rebuild_from_points(conn, args.server, settings)
            except DatabaseBusy as e:
                print(f"error: rebuild not run: {e}. The import is committed; once the "
                      f"snapshot is stopped run: import_backups.py --server {args.server} "
                      "--rebuild-only")
                return 1
            print(f"rebuild: {counts} in {time.time() - t0:.1f}s")
        elif not args.dry_run:
            print("nothing imported: no DB backup, no rebuild")
    finally:
        conn.close()
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
