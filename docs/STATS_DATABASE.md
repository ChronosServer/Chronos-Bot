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
bot.

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
  up "CMPs".
- **Bot filtering:** applied to SMP only, by team membership (no team = bot). CMPs
  never filter bots.
- **`excluded_servers`:** never tracked. mirror and tour are copies of SMP, so tracking
  them would double-count everything.
- **Worlds not created yet:** a configured server whose world folder doesn't exist yet
  (e.g. snapshot, building2) is skipped as "not provisioned", not treated as an error.
- **`track_stats` / `track_scoreboards`:** turn tracking on or off per server.

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
# 1. Install the stats dependencies only. On a machine that also runs the bot, never
#    `pip install -r requirements.txt`: it re-pins the bot's packages.
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
- **Name lookups:** starts filling in missing player names from Mojang, about 60
  lookups per run.

## 3. Upgrading an existing database

Pull the new code, then run the snapshot once by hand **before** restarting the API.
The run applies the new migrations and indexes, and the API checks for those indexes
when it starts.

```bash
cd /opt/chronos/Chronos-Bot && git pull
systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service
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

## 7. Developing locally

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
