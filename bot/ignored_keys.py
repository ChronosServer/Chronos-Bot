"""Stat keys that aren't real (settings snapshot.ignored_stat_keys, default
minecraft:air / cave_air / void_air, in every category): the snapshot and
the import never store them, and rows stored before are deleted once here.
`crafted/minecraft:air` is always 0, `used/minecraft:air` holds small junk
counts; neither is an item anyone has. Meta `ignored_stat_keys` remembers
the list applied, so editing it in config re-runs the cleanup."""
import json

META = "ignored_stat_keys"
_TABLES = ("stat_snapshots", "stat_current", "stat_totals", "stat_totals_current",
           "rivalries", "spotlight_candidates")


def apply_ignored(conn, keys):
    """Deletes stored rows of `keys` if this list wasn't applied yet; returns
    {table: rows} (None if nothing to do). Commits."""
    want = json.dumps(sorted(set(keys)))
    row = conn.execute("SELECT value FROM meta WHERE key=?", (META,)).fetchone()
    if row and row[0] == want:
        return None
    out = {}
    if keys:
        marks = ",".join("?" * len(keys))
        for tbl in _TABLES:
            out[tbl] = conn.execute(f"DELETE FROM {tbl} WHERE stat_key IN ({marks})",
                                    list(keys)).rowcount
        for tbl in ("firsts", "milestones"):  # scoreboard rows use stat_key = objective
            out[tbl] = conn.execute(f"DELETE FROM {tbl} WHERE category != 'scoreboard' "
                                    f"AND stat_key IN ({marks})", list(keys)).rowcount
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                 "DO UPDATE SET value=excluded.value", (META, want))
    conn.commit()
    return out
