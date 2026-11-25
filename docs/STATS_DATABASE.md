# Stats database: setup, upgrade and maintenance

How to set up the SQLite database behind the Chronos website's Stats pages, keep it
running, and recover it. For what the data means, see `STATS_PLAN.md`; for the HTTP
API on top of it, see `STATS_API.md`; for the design and the rules, see `CLAUDE.md`.

Commands assume a Linux server, running as root, with the repo at
`/opt/chronos/Chronos-Bot` and Python at `/opt/python/bin/python3`. Adjust the paths
for your machine. The `sqlite3` command-line tool is not needed: everything uses
Python's built-in `sqlite3`.

## How it works

```
systemd timer (every 15 min)
  └─ bot/snapshot.py   reads each world's players/stats/*.json + data/minecraft/scoreboard.dat
       ├─ writes history: a row only when a value changed (change-only)
       └─ bot/derive.py   activity, totals, firsts, milestones, badges, rivalries, spotlight
SQLite database (WAL mode)
  └─ api/  (FastAPI, opens the DB read-only) → nginx /api/stats/ → website
```

- **Creating and upgrading the database is automatic.** `snapshot.py` creates the file
  and all tables on its first run. On every run it applies any pending migrations
  (`sql/migrations.py`, tracked in `PRAGMA user_version`). There is no separate
  "create database" step.
- **Only the snapshot ever writes.** The API opens the database read-only and refuses to
  start until a snapshot has created every index it needs.
- **Size:** roughly 130 MB after the first full snapshot of all servers. It grows with
  actual in-game changes, not with time.

## 1. Configure

Add a `stats_tracking` block to `config.json`. The servers listed here must also have
`server.<key>_path` and `server.<key>_world_name` entries, which already exist for the
bot. Only `db_path` and `servers` are needed; this is the live server's block:

```json
"stats_tracking": {
  "db_path": "/opt/chronos/stats/chronos-stats.db",
  "excluded_servers": ["mirror", "tour", "snapshot", "building2"],
  "servers": {
    "smp":      {"label": "SMP",      "group": "smp", "track_stats": true, "track_scoreboards": true},
    "cmp":      {"label": "CMP",      "group": "cmp", "track_stats": true, "track_scoreboards": true},
    "cmp2":     {"label": "CMP2",     "group": "cmp", "track_stats": true, "track_scoreboards": true},
    "cmp3":     {"label": "CMP3",     "group": "cmp", "track_stats": true, "track_scoreboards": true},
    "cmp4":     {"label": "CMP4",     "group": "cmp", "track_stats": true, "track_scoreboards": true},
    "building": {"label": "BUILDING", "group": "cmp", "track_stats": true, "track_scoreboards": true}
  }
}
```

- **`db_path`:** where the database lives. Its folder is created if missing.
- **`group`:** decides the website scopes. `smp` is SMP; everything in group `cmp` makes
  up "CMPs" (see `scope_groups` below).
- **Bot filtering (`filters_bots`):** applied to SMP only, by team membership (no team =
  bot). CMPs never filter. That is the owner's rule and the default; leave it alone
  unless the owner changes the rule.
- **`excluded_servers`:** never tracked. mirror and tour are copies of SMP, so tracking
  them would double-count everything.
- **Worlds not created yet:** a configured server whose world folder doesn't exist yet
  (e.g. snapshot, building2) is skipped as "not provisioned", not treated as an error.
- **`track_stats` / `track_scoreboards`:** turn tracking on or off per server. Off means
  the snapshot stops reading it **and** the API hides that server's already collected
  stats / scoreboards everywhere (leaderboards, series, totals, player, search, keys,
  feeds). Nothing is deleted: turning it back on shows the old data again. The change
  takes effect at the next snapshot run (no restart).
- **Order:** the order of the `servers` entries is the order the website lists servers
  and scopes in. Reorder the entries to reorder the site (next snapshot run).

### Tuning (all optional)

Every other key is optional. A missing key uses the built-in default, which is the value
the code has always used, so a block like the one above behaves exactly as before.
Defaults live in `api/settings.py` (`DEFAULT_CONFIG`). Override only what you want to
change: objects merge key by key, so `"rivalries": {"max_gap_ratio": 0.05}` keeps every
other rivalry default. Lists and single values replace the default. The maps whose keys
are names you choose (`servers`, `scope_groups`, `api.range_presets`) replace the
default map as a whole, so you can drop a name. Keys starting with `_` are ignored, so
you can leave notes like `"_comment": "..."`.

The full block with every key at its default:

```json
"stats_tracking": {
  "db_path": "/opt/chronos/stats/chronos-stats.db",
  "excluded_servers": [],
  "servers": {
    "smp": {"label": "SMP", "group": "smp", "track_stats": true, "track_scoreboards": true,
            "filters_bots": true},
    "cmp": {"label": "CMP", "group": "cmp", "track_stats": true, "track_scoreboards": true,
            "filters_bots": false}
  },
  "scope_groups": {"cmps": "cmp"},
  "activity_stat": ["minecraft:custom", "minecraft:play_time"],
  "milestones": {
    "count_categories": ["minecraft:mined", "minecraft:used", "minecraft:crafted",
                         "minecraft:broken", "minecraft:picked_up", "minecraft:dropped",
                         "minecraft:killed", "minecraft:killed_by"],
    "count_thresholds": [1000, 10000, 100000, 1000000, 10000000],
    "per_key": [
      {"category": "minecraft:custom", "key": "minecraft:play_time",
       "thresholds": [100, 500, 1000, 5000, 10000], "scale": 72000},
      {"category": "minecraft:custom", "key": "minecraft:walk_one_cm",
       "thresholds": [100, 1000, 10000], "scale": 100000},
      {"category": "minecraft:custom", "key": "minecraft:deaths",
       "thresholds": [100, 1000, 10000], "scale": 1}
    ]
  },
  "badges": {
    "firsts": [1, 10, 50],
    "top1": [1, 10, 50],
    "big_numbers": [1000000, 10000000],
    "category_tiers": [100000, 1000000, 10000000],
    "veteran_hours": 1000,
    "regular": {"days": 20, "window_days": 30},
    "night_owl": {"min_buckets": 40, "window_days": 30, "share": 0.5,
                  "start_hour": 0, "end_hour": 6}
  },
  "rivalries": {"min_value": 1000, "max_gap_ratio": 0.02, "active_days": 7,
                "per_player": 5, "per_key": 3},
  "spotlight": {
    "outlier": {"ratio": 10, "min_holders": 10},
    "rare": {"max_holders": 3},
    "recent_milestone_days": 7,
    "recent_first_days": 30,
    "mover": {"top": 20, "min_growth": 0.5, "min_delta": 1000, "window_days": 7}
  },
  "mojang": {"max_calls": 60, "timeout_s": 3, "max_seconds": 60, "retry_after_days": 7},
  "snapshot": {"dictionary_cache_hours": 24, "dictionary_timeout_s": 15},
  "api": {
    "range_presets": {"1d": 86400, "1w": 604800, "1m": 2592000, "6m": 15724800,
                      "1y": 31536000},
    "activity_bucket_s": 900,
    "series_max_points": 500,
    "leaderboard_max_limit": 200,
    "feed_max_limit": 200,
    "search_max_limit": 50,
    "compare_min_players": 2,
    "compare_max_players": 6,
    "compare_max_keys": 20,
    "series_max_players": 6,
    "player_milestones_limit": 50,
    "cache_max_bytes": 134217728,
    "cache_max_body_bytes": 8388608,
    "cache_max_entries": 2048,
    "cache_max_age_s": 60,
    "cache_stale_while_revalidate_s": 900,
    "gzip_min_bytes": 1000,
    "sqlite_mmap_bytes": 268435456,
    "sqlite_cache_kib": 65536
  }
}
```

(`db_path` has no default and `excluded_servers` defaults to empty; the `servers`
entries above show the per-server defaults, not a server list you get for free.)

| Block | What it controls |
|---|---|
| `servers.<key>.filters_bots` | Bot filtering and the team check (in a team = real player). Default `true` for `smp`, `false` for every other key. CMPs never filter, by the owner's decision. |
| `scope_groups` | Extra website scopes: scope name → `servers.<key>.group`. The scopes are `all`, these names, and every provisioned server key. A name can't be `all` or a server key. |
| `activity_stat` | The play-time stat (ticks). A change in it marks a player active (activity heatmap, Regular, Night Owl, rivalries), and `badges.veteran_hours` is measured on it. |
| `milestones` | Milestone rules. `count_categories` × `count_thresholds` give one `cat:<category>` rule each; `per_key` rules are `key:<category>/<key>`, stored threshold = `threshold × scale` (72000 ticks per hour, 100000 cm per km). |
| `badges` | Badge thresholds. Firsts and Champion tiers, the Millionaire thresholds (`big_numbers`: only values that are also `count_thresholds` can ever match), category tiers (categories = `milestones.count_categories`), Veteran hours, Regular (`days` of the last `window_days`), Night Owl (≥ `min_buckets` active 15-min buckets in `window_days`, more than `share` of them in [`start_hour`, `end_hour`) server time). |
| `rivalries` | Leader value ≥ `min_value`, gap ≤ `max_gap_ratio` × leader, both active within `active_days`, at most `per_player` per player and `per_key` per stat. The gap comparison window stays 7 days (it is what `gap_7d_ago` means). |
| `spotlight` | Outlier (≥ `ratio` × median with ≥ `min_holders` holders), rare (≤ `max_holders` holders), how recent a milestone/first must be, and movers (top `top` gains over `window_days`, gain ≥ `min_delta` and ≥ `min_growth` × the start value). |
| `mojang` | Name lookups per run: at most `max_calls` calls, `timeout_s` per call, `max_seconds` in total; every result (also failures) is cached for `retry_after_days`. |
| `snapshot` | How long the scoreboard dictionary from GitHub is cached, and its fetch timeout. |
| `api` | Range presets, activity bucket size, the hard caps on `limit`, compare and series sizes, the `/player` milestone count, series points, the response cache (size, entries, `Cache-Control` max-age / stale-while-revalidate), gzip threshold and SQLite read pragmas. |

**When changes take effect:**
- **Snapshot (everything except `api`):** on the next run (the timer runs it every 15
  minutes). A bad value makes the run fail at the start with a message naming the key,
  such as `stats_tracking.rivalries.max_gap_ratio must be a number in (0, 1]`, before it
  touches the database. Check `journalctl -u chronos-stats-snapshot` after an edit.
- **API (`api`, `scope_groups`, and the badge values shown in `/meta.settings`):** only
  after `systemctl restart chronos-stats-api`. The API reads `config.json` once at
  start, and refuses to start on a bad value.
- **Milestone rules** are synced on every run: changed thresholds are updated, rules you
  remove are switched off (`enabled = 0`), and nothing is deleted. New thresholds are
  **not** backfilled: they only catch crossings from the next change on. Players who
  were already past a new threshold don't get it.
- **Badges** are kept once earned. Changing a threshold changes the badge key (e.g.
  `firsts:10` → `firsts:20`), and badges already awarded under the old key stay.
- **Rivalries and spotlight** are rebuilt on every run, so changes show up fully on the
  next run.

Expected world layout, verified on the live server:
```
<server>_path/
  usercache.json
  <server>_world_name/
    players/stats/<uuid>.json
    data/minecraft/scoreboard.dat
```

## 2. First setup on a new server

```bash
cd /opt/chronos/Chronos-Bot
# 1. Install the stats dependencies. `pip install -r requirements.txt` also works, but on
#    a machine that also runs the bot its exact pins move the bot's packages: --dry-run first.
/opt/python/bin/python3 -m pip install nbt==1.5.1 'fastapi==0.165.0' 'uvicorn[standard]==0.54.0' 'orjson==3.13.0'

# 2. Create and fill the database with the first snapshot. Everything counts as "new"
#    on this run, so it takes longer; later runs take a few seconds.
time /opt/python/bin/python3 bot/snapshot.py

# 3. Schedule it every 15 minutes
cp deploy/systemd/chronos-stats-snapshot.service deploy/systemd/chronos-stats-snapshot.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chronos-stats-snapshot.timer

# 4. Start the API
cp deploy/systemd/chronos-stats-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chronos-stats-api
journalctl -u chronos-stats-api -n 20 --no-pager
```

Then add `deploy/nginx/chronos-stats-api.conf` inside the site's `server { }` block and
run `nginx -t && systemctl reload nginx`.

What the first run does:
- **Tracking start:** marks when tracking began. Time ranges on the website start there,
  and nothing earlier exists.
- **Earliest-known seeding:** every first and milestone that already exists gets
  `earliest_known = 1`. The website shows these as "earliest known holder" or "already
  reached", never with a fake date.
- **Name lookups:** starts filling in missing player names from Mojang, up to
  `mojang.max_calls` (60) lookups per run.

## 3. Upgrading an existing database

Use the update script. It does everything below in the right order and keeps
`config.json` untouched:

```bash
cd /opt/chronos/Chronos-Bot
bash deploy/update.sh --check   # what would change + config check; changes nothing
bash deploy/update.sh
```

When stats code changed, it stops the timer, waits for a running snapshot, updates the
code, runs the snapshot once by hand (this applies new migrations and indexes; the API
checks for those indexes when it starts), restarts the API if `api/` or `sql/` changed,
checks that the API answers, and starts the timer again. The log goes to
`/opt/chronos/update-logs/`. The current schema version is 4 (`PRAGMA user_version`).

The manual equivalent, if you ever need it:

```bash
cd /opt/chronos/Chronos-Bot
systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service
git pull   # config.json is tracked and modified here: set it aside first, put it back after
/opt/python/bin/python3 bot/snapshot.py; systemctl start chronos-stats-snapshot.timer
systemctl restart chronos-stats-api
```

Stopping the service as well as the timer matters: stopping a timer doesn't stop a run
that's already in progress, and two runs at once can hit "database is locked".

## 4. Checking that it's healthy

Each run records one row per server and step in `snapshot_runs`, plus a `derive` row.
Look at the latest ones:

```bash
/opt/python/bin/python3 - <<'PY'
import sqlite3
c = sqlite3.connect("file:/opt/chronos/stats/chronos-stats.db?mode=ro", uri=True)
for r in c.execute("SELECT id, datetime(at,'unixepoch'), server_key, kind, ok, error, "
                   "entries_read, rows_written, duration_ms FROM snapshot_runs "
                   "ORDER BY id DESC LIMIT 15"):
    print(r)
PY
```

What healthy looks like:
- `ok = 1` on every row.
- `entries_read` is not 0 for any tracked server.
- `rows_written` is small when nobody played.
- A `derive` row with `ok = 1`.

Also worth checking:
- `systemctl list-timers chronos-stats-snapshot.timer` shows the next run.
- `journalctl -u chronos-stats-snapshot -n 50` shows the last runs' output.

Common problems:

| Symptom | Cause / fix |
|---|---|
| `entries_read = 0` for a server | Wrong `<key>_path` / `<key>_world_name`, or the world isn't where the layout above says |
| API won't start: "missing indexes" | Run `bot/snapshot.py` once (section 3) |
| `derive` row with `ok = 0` | The `error` column says why. The snapshot data is still saved, and the next run tries derive again |
| "database is locked" | Two snapshot runs at once. Stop the service as well as the timer before a manual run |
| SMP players missing from leaderboards | They're not in any scoreboard team, so they count as bots (intended). The site's "include bots" toggle shows them |

## 5. Backup and restore

The database is in WAL mode, so don't just copy the file while a snapshot is running.
Use SQLite's online backup instead, which is safe at any time:

```bash
/opt/python/bin/python3 -c "
import sqlite3, time
src = sqlite3.connect('/opt/chronos/stats/chronos-stats.db')
dst = sqlite3.connect('/opt/chronos/stats/backup-%s.db' % time.strftime('%Y%m%d-%H%M'))
src.backup(dst); dst.close(); src.close(); print('ok')"
```

To restore:
1. Stop the timer, the snapshot service and the API.
2. Replace `chronos-stats.db` with the backup, and delete any `-wal` / `-shm` files next
   to it.
3. Start the timer and the API again.

## 6. Starting over (reset)

You normally never need this. The history is the whole point, and upgrades happen in
place. If you do want tracking to start fresh, move the database aside instead of
deleting it:

```bash
systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service chronos-stats-api
mv /opt/chronos/stats/chronos-stats.db /opt/chronos/stats/chronos-stats.db.old
rm -f /opt/chronos/stats/chronos-stats.db-wal /opt/chronos/stats/chronos-stats.db-shm
cd /opt/chronos/Chronos-Bot && /opt/python/bin/python3 bot/snapshot.py
systemctl start chronos-stats-snapshot.timer chronos-stats-api
```

After a reset:
- All history before the reset is gone from the website.
- Tracking starts again from now.
- Existing firsts and milestones are seeded as "earliest known" again.

## 7. Importing old world backups

Old backup world folders (1.16 – 1.21 and 26.x) can be imported as earlier history
points, so graphs, totals, firsts and milestones reach back to the oldest backup. Use the
command-line tool `tools/import_backups.py`:

- Stop `chronos-stats-snapshot.timer` and `.service` first, and start the timer again
  afterwards.
- Always run it with `--dry-run` first.
- It backs up the database to `pre-import-*.db` before writing. Restoring that file undoes
  the import.

The full guide covers every flag, how to read the dry-run output, the rename table, the
bundled dictionaries and a worked example: [tools/README.md](../tools/README.md).

## 8. Developing locally

The tests build small fake worlds and run the real snapshot and derive code against
them. Nothing touches the network or a real database.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
```

Notes for changing the schema:
- **New tables or indexes** on existing columns: add them to `sql/schema.sql`
  (`CREATE … IF NOT EXISTS`).
- **New columns, or indexes on new columns:** add a new step in `sql/migrations.py`.
- **Indexes the API pins with `INDEXED BY`** must also be listed in
  `api/db.py REQUIRED_INDEXES`.
