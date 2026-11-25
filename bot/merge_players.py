"""Fold an account into another (player_merges.json), once per entry.

Run by snapshot.run_once before any server is read, so the live run that
first sums the two accounts starts from already merged data (merging after a
live run had summed them would count the from-account twice). Meta
`merged:<from>` = <to> marks an entry as done; removing an entry later does
not split the data (restore the pre-merge-*.db backup made here).

What moves from A (from) into B (to):
- stat_snapshots / scoreboard_snapshots: per (server, key), A's and B's
  change-only timelines are replaced by one timeline of their SUM under B
  (value at T = A's at T + B's at T, a row wherever the sum changes).
  Scoreboard rows of A are uuid=A rows plus unresolved rows under one of
  the entry's `names` (scoreboards key by name).
- stat_current / scoreboard_current: B += A, A's rows removed.
- firsts -> B; milestones -> B (the earlier one kept when both crossed);
  badges, activity -> B (duplicates dropped); rivalries / spotlight rows and
  player_flags of A dropped (recomputed); B's first_seen / last_activity
  widened; totals history rebuilt for the servers touched.
Milestones the SUM crossed before the merge but neither account did alone
are not backfilled; later crossings are found as usual.

Renames (player_merges.json `renames`, one account's former names) fold the
same way, once per (uuid, name) (meta `renamed:<uuid>:<name>`): the name's
unresolved scoreboard rows inside the entry's from/until are summed into the
uuid's timeline; after `until` the name counts 0 for the uuid (from its first
later row). Its `unresolved:<name>` current rows move to the uuid only when
the entry covers now.
"""
import sqlite3
import time
from pathlib import Path

META = "merged:"
RENAMED = "renamed:"


def backup_db(conn, db_path, now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() if now is None else now))
    dest_path = Path(db_path).parent / f"pre-merge-{stamp}.db"
    dest = sqlite3.connect(str(dest_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return dest_path


def pending(conn, merges):
    done = dict(conn.execute("SELECT key, value FROM meta WHERE key LIKE 'merged:%'"))
    out = []
    for a, b in sorted(merges.to_of.items()):
        prev = done.get(META + a)
        if prev is None:
            out.append((a, b))
        elif prev != b:
            print(f"[merge] {a} was already merged into {prev}; now listed into {b}: "
                  "not merged again (restore a pre-merge backup to change it)")
    return out


def sum_timelines(series):
    """series: lists of (at, value) per source, each in time order. Returns
    the change-only timeline [(at, sum)] of their sum (a source counts 0
    before its first row; at one `at` a source's last row wins)."""
    events = sorted((at, i, v) for i, s in enumerate(series) for at, v in s)
    cur = [0] * len(series)
    out, last, k = [], None, 0
    while k < len(events):
        at = events[k][0]
        while k < len(events) and events[k][0] == at:
            cur[events[k][1]] = events[k][2]
            k += 1
        total = sum(cur)
        if total != last:
            out.append((at, total))
            last = total
    return out


def _merge_stats(conn, a, b):
    rows = {}
    for who in (a, b):
        for sk, c, k, at, v in conn.execute(
                "SELECT server_key, category, stat_key, at, value FROM stat_snapshots "
                "WHERE uuid=? ORDER BY at, id", (who,)):
            rows.setdefault((sk, c, k), {}).setdefault(who, []).append((at, v))
    servers, written = set(), 0
    for (sk, c, k), by in rows.items():
        if a not in by:
            continue  # B only: unchanged
        merged = sum_timelines([by.get(a, []), by.get(b, [])])
        conn.execute("DELETE FROM stat_snapshots WHERE server_key=? AND category=? AND "
                     "stat_key=? AND uuid IN (?, ?)", (sk, c, k, a, b))
        conn.executemany(
            "INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
            "VALUES (?, ?, ?, ?, ?, ?)", [(at, sk, b, c, k, v) for at, v in merged])
        servers.add(sk)
        written += len(merged)
    for sk, c, k, v, upd, seen in conn.execute(
            "SELECT server_key, category, stat_key, value, updated_at, seen_at "
            "FROM stat_current WHERE uuid=?", (a,)).fetchall():
        conn.execute(
            "INSERT INTO stat_current (server_key, uuid, category, stat_key, value, updated_at, "
            "seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(server_key, uuid, category, "
            "stat_key) DO UPDATE SET value=value+excluded.value, "
            "updated_at=MAX(updated_at, excluded.updated_at), seen_at=MAX(seen_at, excluded.seen_at)",
            (sk, b, c, k, v, upd, seen))
        servers.add(sk)
    conn.execute("DELETE FROM stat_current WHERE uuid=?", (a,))
    return servers, written


def _merge_scoreboards(conn, a, b, names, b_name, from_ts=None, until_ts=None,
                       move_current=True):
    """a=None: a rename (only B's rows and the names' unresolved rows).
    from_ts/until_ts bound which unresolved name rows are folded (rows
    outside stay unresolved; after until_ts a name counts 0 from its first
    later row). move_current: add the names' unresolved current rows."""
    names = sorted(names)
    marks = ",".join("?" * len(names))
    lo = -1 if from_ts is None else from_ts
    hi = 2 ** 62 if until_ts is None else until_ts
    name_sql = (f" OR (uuid IS NULL AND lower(raw_name) IN ({marks}) AND at BETWEEN ? AND ?)"
                if names else "")
    name_p = [*names, lo, hi] if names else []
    rows = {}
    for sk, o, at, v, u, raw in conn.execute(
            "SELECT server_key, objective, at, value, uuid, raw_name FROM scoreboard_snapshots "
            f"WHERE uuid IN (?, ?){name_sql} ORDER BY at, id", (a, b, *name_p)):
        src = u if u else "name:" + raw.lower()
        rows.setdefault((sk, o), {}).setdefault(src, []).append((at, v))
    if names and until_ts is not None:
        # the name is someone else's after until_ts: it stops counting for B
        for (sk, o), by in rows.items():
            for src, series in by.items():
                if src.startswith("name:") and series[-1][1]:
                    nxt = conn.execute(
                        "SELECT MIN(at) FROM scoreboard_snapshots WHERE server_key=? AND "
                        "objective=? AND uuid IS NULL AND lower(raw_name)=? AND at>?",
                        (sk, o, src[5:], until_ts)).fetchone()[0]
                    if nxt is not None:
                        series.append((nxt, 0))
    servers, written = set(), 0
    for (sk, o), by in rows.items():
        if set(by) == {b}:
            continue  # B only: unchanged
        merged = sum_timelines(list(by.values()))
        conn.execute(
            "DELETE FROM scoreboard_snapshots WHERE server_key=? AND objective=? AND "
            f"(uuid IN (?, ?){name_sql})", (sk, o, a, b, *name_p))
        conn.executemany(
            "INSERT INTO scoreboard_snapshots (at, server_key, participant_kind, uuid, raw_name, "
            "objective, value) VALUES (?, ?, 'player', ?, ?, ?, ?)",
            [(at, sk, b, b_name, o, v) for at, v in merged])
        servers.add(sk)
        written += len(merged)
    keys = ([a] if a else []) + ([r[0] for r in conn.execute(
        "SELECT DISTINCT participant_key FROM scoreboard_current WHERE participant_key LIKE "
        "'unresolved:%'") if r[0][len("unresolved:"):].lower() in set(names)]
        if move_current else [])
    for pk in keys:
        for sk, o, v, upd, seen in conn.execute(
                "SELECT server_key, objective, value, updated_at, seen_at FROM scoreboard_current "
                "WHERE participant_key=?", (pk,)).fetchall():
            conn.execute(
                "INSERT INTO scoreboard_current (server_key, participant_key, objective, value, "
                "updated_at, seen_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(server_key, "
                "participant_key, objective) DO UPDATE SET value=value+excluded.value, "
                "updated_at=MAX(updated_at, excluded.updated_at), "
                "seen_at=MAX(seen_at, excluded.seen_at)", (sk, b, o, v, upd, seen))
            servers.add(sk)
        conn.execute("DELETE FROM scoreboard_current WHERE participant_key=?", (pk,))
    return servers, written


def _merge_derived(conn, a, b):
    conn.execute("UPDATE firsts SET uuid=? WHERE uuid=?", (b, a))
    # both crossed the same milestone: keep the earlier one
    conn.execute(
        "DELETE FROM milestones WHERE uuid=? AND EXISTS (SELECT 1 FROM milestones m "
        "WHERE m.uuid=? AND m.server_key=milestones.server_key AND m.category=milestones.category "
        "AND m.stat_key IS milestones.stat_key AND m.threshold=milestones.threshold "
        "AND m.at < milestones.at)", (b, a))
    for tbl in ("milestones", "badges", "activity"):
        conn.execute(f"UPDATE OR IGNORE {tbl} SET uuid=? WHERE uuid=?", (b, a))
        conn.execute(f"DELETE FROM {tbl} WHERE uuid=?", (a,))
    conn.execute("DELETE FROM rivalries WHERE uuid_a=? OR uuid_b=?", (a, a))
    conn.execute("DELETE FROM spotlight_candidates WHERE uuid=?", (a,))
    conn.execute("DELETE FROM player_flags WHERE uuid=?", (a,))
    pa = conn.execute("SELECT first_seen, last_activity, last_observed FROM players WHERE uuid=?",
                      (a,)).fetchone()
    if pa:
        conn.execute(
            "INSERT INTO players (uuid, name, name_resolved, first_seen, last_activity, "
            "last_observed) VALUES (?, NULL, 0, ?, ?, ?) ON CONFLICT(uuid) DO UPDATE SET "
            "first_seen=MIN(first_seen, excluded.first_seen), "
            # the later of the two; NULL (never active) only if both are NULL
            "last_activity=COALESCE(MAX(last_activity, excluded.last_activity), "
            "last_activity, excluded.last_activity)",
            (b, pa[0], pa[1], pa[2]))


def pending_renames(conn, merges):
    """Rename entries (api.merges.Rename) not folded yet."""
    done = {r[0] for r in conn.execute("SELECT key FROM meta WHERE key LIKE 'renamed:%'")}
    return [r for r in merges.renames if f"{RENAMED}{r.uuid}:{r.name}" not in done]


def _fold_rename(conn, r, run_at):
    b = r.uuid
    row = conn.execute("SELECT name FROM players WHERE uuid=?", (b,)).fetchone()
    b_name = row[0] if row and row[0] else r.display
    conn.execute("INSERT OR IGNORE INTO players (uuid, name, name_resolved, first_seen, "
                 "last_observed) VALUES (?, NULL, 0, ?, ?)", (b, run_at, run_at))
    servers, n = _merge_scoreboards(conn, None, b, {r.name}, b_name, r.from_ts, r.until_ts,
                                    move_current=r.covers(run_at))
    conn.execute("INSERT INTO meta (key, value) VALUES (?, '1') ON CONFLICT(key) "
                 "DO UPDATE SET value=excluded.value", (f"{RENAMED}{b}:{r.name}",))
    print(f"[rename] {r.display} -> {b}: {n} scoreboard history rows "
          f"(servers: {', '.join(sorted(servers)) or 'none'})")
    return servers


def apply_pending_merges(conn, merges, run_at, db_path, *, backup=True):
    """Folds every not-yet-applied merge and rename in. One transaction;
    returns the list of (from, to) merges applied."""
    todo = pending(conn, merges)
    renames = pending_renames(conn, merges)
    if not todo and not renames:
        return []
    if backup:
        print(f"[merge] DB backup: {backup_db(conn, db_path)}")
    import derive  # bot/ is on sys.path when the snapshot runs
    conn.execute("BEGIN IMMEDIATE")
    try:
        touched = set()
        for a, b in todo:
            row = conn.execute("SELECT name FROM players WHERE uuid=?", (b,)).fetchone()
            b_name = row[0] if row and row[0] else b
            # B must exist in players before rows reference it (FK)
            conn.execute("INSERT OR IGNORE INTO players (uuid, name, name_resolved, first_seen, "
                         "last_observed) VALUES (?, NULL, 0, ?, ?)", (b, run_at, run_at))
            s1, n1 = _merge_stats(conn, a, b)
            s2, n2 = _merge_scoreboards(conn, a, b, merges.names_of.get(a, set()), b_name)
            _merge_derived(conn, a, b)
            touched |= s1 | s2
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                         "DO UPDATE SET value=excluded.value", (META + a, b))
            print(f"[merge] {a} -> {b}: {n1} stat + {n2} scoreboard history rows "
                  f"(servers: {', '.join(sorted(s1 | s2)) or 'none'})")
        for r in renames:
            touched |= _fold_rename(conn, r, run_at)
        if touched:
            derive.rebuild_totals(conn, run_at, sorted(touched))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return todo
