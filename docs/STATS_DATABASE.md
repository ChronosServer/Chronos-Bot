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
  "heads": {"enabled": true, "max_calls": 60, "max_seconds": 30, "timeout_s": 5,
            "refresh_hours": 24, "ipv4_only": true},
  "snapshot": {"dictionary_cache_hours": 24, "dictionary_timeout_s": 15,
               "mirror_prefix": "^(m|u|c|b|p|d|k|kb|z)-",
               "ignored_stat_keys": ["minecraft:air", "minecraft:cave_air", "minecraft:void_air"]},
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
| `heads` | Player heads (`<server.website_path>/cache/heads/<uuid>.png`), the last step of every snapshot run: at most `max_calls` Mojang profile lookups and `max_seconds` per run, `timeout_s` per call; players never checked go first, then everyone again every `refresh_hours`. A rate limit, a network error or any Mojang error answer ends that run's step and changes nothing (existing heads stay); only "no such profile" or "no skin" removes a head. `enabled: false` turns it off. `ipv4_only` (default true): connect over IPv4 only (Mojang's session server answers 429 to every request from the live server's IPv6, which the Minecraft servers share; found 2026-11-26). Skipped when `server.website_path` is missing. |
| `snapshot` | How long the scoreboard dictionary from GitHub is cached, and its fetch timeout. `mirror_prefix`: a regular expression; objectives whose name matches it are every-scoreboard copies of vanilla stats (`m-stone`, `z-play_one_m+748`, `u-moss`) even when no bundled dictionary lists them, so their scores aren't stored and their firsts/milestones are hidden. `""` turns it off (dictionary only). `ignored_stat_keys`: stat keys that aren't real items (default: the three air blocks), in every category: never stored, and rows stored before are deleted once on the next run (again whenever the list changes). |
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

### Player names
- A player's name is the **newest** one across every tracked server's `usercache.json`
  (by `expiresOn`). A renamed player can still be listed under the old name on a server
  they haven't joined since; that never wins.
- Players no live usercache knows (e.g. only known from a backup) get their current name
  from Mojang with whatever is left of the per-run Mojang budget; answers are cached for
  `mojang.retry_after_days`, so names catch up over a few runs.
- A rename needs nothing: everything is keyed by uuid. In imported backups, a renamed
  player's old scoreboard names are summed under the current name.

### Merging two accounts (`player_merges.json`)
For separate accounts that should count as one player (an alt or a new account; not for a
rename). The file sits at the repo root, so it is deployed with the code:
```json
{"merges": [{"from": "<alt uuid>", "to": "<main uuid>", "names": ["AltScoreboardName"],
             "note": "Alt -> Main"}]}
```
`names` are the alt's scoreboard names (scoreboards key by name). Commit, push and run
`bash deploy/update.sh`. The next snapshot run backs up the DB (`pre-merge-<stamp>.db`
next to it) and then, once per new entry, adds the alt's history, current values, firsts,
milestones, badges and activity into the main account and rebuilds totals (meta
`merged:<alt uuid>`). From then on the live snapshot and the backup import sum them, and
the website shows the main player for the alt's name. Removing an entry does not split the
data again: restore that backup instead.

### Former names (`player_merges.json` `renames`)
For ONE account that was renamed and whose old name no usercache knows any more (scoreboards
key by name, so its old scores sit unlinked):
```json
{"renames": [{"uuid": "<uuid>", "names": ["OldName_"], "from": "2021-01-01",
              "until": "2024-06-30", "note": "OldName_ -> NewName"}]}
```
`from`/`until` are optional (UTC dates, inclusive): when the name was this player's (for a
name someone else used later). `"case_sensitive": true` matches the exact spelling only (when
a Carpet bot uses the same name in another case, e.g. `NotAless50` vs the bot `notaless50`). The next snapshot run folds the name's existing scoreboard
history into the player once (meta `renamed:<uuid>:<name>`, same DB backup as merges); from
then on the live snapshot and the import count that name for the player. The website lists
former names as "aka" and finds the player by them. On a server with imported backups, then
run `python3 tools/import_backups.py --server smp --reimport-scoreboards` (timer stopped,
`--dry-run` first; the snapshot prints this as a WARNING). Needed: imported history is
change-only and can't tell "the old name is gone from this backup" (after an in-game
stattransfer) from "unchanged", so the fold alone keeps the old name's last backup value
until the first live run; the reimport re-reads each backup with the rename applied, then
re-derives the approximate firsts/milestones.
Editing a folded entry's `from`/`until`/`case_sensitive` later changes nothing (the fold
already happened; the meta value records the folded range). Every snapshot run then prints
`[rename] WARNING: ... changed after it was folded`: put the old values back, or restore the
`pre-merge-*.db` made before that fold (the next run folds it again with the new range).

A name a server's own usercache doesn't know (a player renamed, then stattransferred there
before rejoining it) is resolved through every server's usercache. Its live scores stored
unlinked before that are folded into the player on the next run (`[names] ... now resolves
to ...`, with a DB backup); backup-era rows of a name are never folded this way.

### Slur filter (`name_blocklist.txt`, `name_allowlist.txt`)
One term per line at the repo root. A player whose name (or a former name) contains a term,
after normalising (case, `_`, leetspeak, doubled letters), is hidden from the whole API,
even with "include bots"; their numbers stay only in whole-server totals. Applies at the
next snapshot run (table `blocked_players`), no restart. Check what a list catches:
`python3 tools/check_blocklist.py` (read-only); put innocent names or uuids in
`name_allowlist.txt`.

### Player heads and item icons
- **Heads** (`bot/heads.py`): the snapshot writes `<server.website_path>/cache/heads/<uuid>.png`
  (64x64, face + hat) for real accounts, a bounded number per run (`heads` above; table
  `player_heads` = when each uuid was checked and its skin url). No file for fake players,
  slur-blocked players or accounts merged into another (`player_merges.json`; an existing
  file is removed) or players without a skin. The run
  log line: `[all] heads: written=.. checked=.. removed=..`. At the defaults (60 per run, every 15 min)
  a first fill of the ~3,000 players takes about 12 hours (most recently active first, so
  the leaderboard tops get theirs early). nginx needs the `/cache/heads/` location from
  `deploy/nginx/chronos-stats-api.conf` (`try_files $uri =404`): without it a head not
  downloaded yet answers the site's 404 page with status 200.
  Fetched over IPv4 only by default (`ipv4_only`): the live server's IPv6 always gets 429. Needs Pillow (`requirements.txt`); without it the step
  prints an error and the rest of the run is unaffected.
- **Item icons** (`tools/render_items.py`, by hand, after every Minecraft version bump):
  downloads the client jar for `server.minecraft_version` from Mojang (sha1-checked, kept as
  `client-<version>.jar` next to the stats DB), renders every item id into
  `<server.website_path>/cache/items/<id>.png` (overwriting) and prints a report: ids drawn
  with a fallback, Storage page names that are no item id, and old files that are no item
  id (left alone). `--dry-run` renders and reports without writing. The Discord bot's
  storage sync no longer fetches item textures.
  ```
  cd /opt/chronos/Chronos-Bot && /opt/python/bin/python3 tools/render_items.py --dry-run
  cd /opt/chronos/Chronos-Bot && /opt/python/bin/python3 tools/render_items.py
  ```

### First live run = a full observation
A key a backup has but the live files didn't have at the first live snapshot (a reset
stat, a removed objective) gets a 0 row at that first live snapshot, so history never
carries a stale backup value forward. The snapshot writes these once for an existing DB
(meta `live_start_filled`), and every backup import writes them for its server.

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
