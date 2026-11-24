# Chronos-Bot

Discord bot for the Chronos SMP. Python, discord.py, RCON + direct NBT/region-file
reads (not RCON) for most data, since RCON is slow at this scale and several world
files are read directly instead. Python 3.14 recently broke `nest_asyncio` (asyncio
internals changed) — fixed by removing it and using `client.start()` properly inside
`asyncio.run(main())`. If anything else breaks after a Python upgrade, consider
pinning to 3.12/3.13 rather than chasing each incompatibility.

## config.json shape (relevant keys)
`server.*_path` / `server.*_world_name` per tracked world (smp, cmp, cmp2, cmp3,
cmp4, mirror, snapshot, building, building2, tour, public). `server.*_rcon_port` per
server, shared `rcon_pass`. `server.website_path` = where this bot writes JSON for
the website to serve (`<website_path>/data/*.json`). `server.minecraft_version` is
used to pick `dictionary-<version>.json`.

**mirror and tour are near-duplicates of smp** (confirmed: same stat distributions,
same fake-player names, nearly identical scoreboard objective counts) — never track
them as separate entities, anywhere, or activity gets double-counted.

## Fake players (important, specified directly by the account owner)
`luigiserectcock`, `Cane`, etc. are fake-player NPCs used to let automation mine/
place/kill — NOT real accounts, never logged in, never in `usercache.json`. The one
and only rule for "is this a real player": **a uuid/name resolves in usercache.json,
or a stat file exists for that uuid** (stat files are proof of a real join — Minecraft
only creates them for accounts that joined; usercache alone under-counts real
players by ~1,100 on SMP since it only holds recently-cached names). Apply this
identically everywhere a participant is classified — don't invent a second,
different heuristic (e.g. regex on name shape) in a new code path.

Bot filtering is **SMP-only**, via scoreboard team membership: in a team = real
player, in no team = bot. **Strict, no exceptions, confirmed by the owner against
live data** (2026-11-24: ~196 team members; 968 of 1,087 SMP stat-file holders
are bots, e.g. `BedBot`, `Endswitch`, `digsort`, `001`. These are named automation
accounts WITH stat files, a different thing from the unnamed fake-player NPCs
above). Missing flag = bot (fail closed). Default-excluded from leaderboards, toggle to include.
**CMPs never filter bots — always included, no toggle, by explicit decision.**

## commands/syncCommand.py + membersExport.py
Already hardened: every sync step isolated with its own timeout, atomic JSON writes
(`write_json_atomic` → temp file + `os.replace`), loops auto-restart after a crash
instead of dying silently (`tasks.loop` stops permanently on one unhandled
exception — this bit us before, cost ~9 days of stale members data with zero log
output). `/sync_status` shows last-success time and next-run time per loop — check
this first whenever data looks stale, before assuming the sync is broken.

## Stats subsystem (in progress — see bot/snapshot.py, sql/schema.sql)
Change-only SQLite storage: a history row is written ONLY when a value differs from
the last known value (tracked in `stat_current`/`scoreboard_current`). This is load-
bearing, not a style choice — SMP alone has 9,346 scoreboard objectives and 175k
PlayerScores rows; unconditional per-run inserts would be tens of millions of
rows/day. Every query (current value, delta over a range) reads "latest row at/
before timestamp T", never "sum all rows in range".

Vanilla-mirrored scoreboard objectives (per `dictionary-<version>.json`) are tracked
via `scoreboard_meta.is_vanilla_mirror` but their **scores are never inserted into
scoreboard_snapshots** — that data already lives in `stat_snapshots` from the player
stat files; storing both would duplicate ~9,200 of SMP's 9,346 objectives for nothing.

NBT quirk: Paper omits `Objectives`/`PlayerScores`/`Teams` tags entirely when they'd
be empty (confirmed on the `building` world — has Teams, no Objectives at all).
Missing tag = empty list, never an error, and **never** means "every score here was
reset to zero" — `scoreboard_meta.removed` tracks genuine removal as a distinct fact
from "never had this".

**Confirmed real directory layout** (verified against the live server via `find`,
not assumed from naming convention — don't re-guess this):
```
{key}_path (server root)/
  usercache.json                           <- per-server, NOT shared across servers
  {key}_world_name (e.g. world-smp0)/
    players/stats/<uuid>.json              <- NOT world_dir/stats directly
    data/minecraft/scoreboard.dat          <- NOT world_dir/data directly
```
Three real bugs were found and fixed here by actually running against production:
1. `server_paths()` was returning `{key}_path` as-is instead of joining it with
   `{key}_world_name` — every world read failed silently (`read=0`, no error).
2. `usercache.json` was wrongly loaded once from `server.website_path` and shared
   across all servers. It's per-server, at that server's own root.
3. Stat files are under `players/stats/`, not `stats/` directly, and
   `scoreboard.dat` is under `data/minecraft/`, not `data/` directly.

`stats_tracking` config block (add to `config.json`, not yet present by default):
```json
"stats_tracking": {
  "db_path": "/opt/chronos/stats/chronos-stats.db",
  "excluded_servers": ["mirror", "tour"],
  "servers": {
    "smp":  {"label": "SMP",  "group": "smp", "track_stats": true, "track_scoreboards": true},
    "cmp3": {"label": "CMP3", "group": "cmp", "track_stats": true, "track_scoreboards": false}
  }
}
```
`track_stats`/`track_scoreboards` are independent per-server toggles — cmp3/cmp4's
scoreboards are mostly noise (MotX/health/gt/boatnumber), set `track_scoreboards:
false` there rather than filtering client-side.

**Status: `bot/snapshot.py` has been run against the real server twice and verified.**
Run 1 (first-ever run, everything "new"): smp stats read=2097 written=144592,
smp scoreboards read=133793 written=7697 (the ~126k skip gap is the vanilla-mirror
dedup working correctly — confirmed via `scoreboard_meta`: smp shows 9346 total
objectives / 9223 flagged as mirrors, matching the original probe output exactly).
Run 2 (immediately after, nothing changed in-game): every server showed
`rows_written=0` with the same `entries_read` as run 1 — proof the change-only
diffing works against real data, not just synthetic fixtures. `ok=1` on every row
in `snapshot_runs` across both runs, no errors.

Run 3 (after real in-game activity on smp + cmp): smp stats written=61, smp
scoreboards written=13, cmp stats written=30, every other server written=0 —
small, correct deltas on exactly the servers that changed. Change-only diffing is
fully verified; safe to put on a cron/systemd timer alongside the bot's other
sync jobs. (`sqlite3` CLI isn't installed on the server — query the DB via
`python3 -c "import sqlite3; ..."` instead.)

**Stats API + derive step: built (tests green, real-server verification pending).**
- `bot/derive.py` runs at the end of every `snapshot.run_once()`; a derive failure
  is recorded as `snapshot_runs.kind='derive'`, `ok=0` and never fails the snapshot.
  New tables: `activity`, `stat_totals`/`scoreboard_totals` (+ `_current`), `meta`,
  `milestones`/`milestone_rules`, `firsts`, `badges`, `rivalries`,
  `spotlight_candidates`, `name_lookups`, `dictionary_cache`. Column additions go
  through `sql/migrations.py` (`PRAGMA user_version`), not schema.sql (schema.sql runs
  first, so anything touching an added column — e.g. `idx_milestones_feed` — lives there).
- SMP team check is strict: no team = bot, **missing `player_flags` row on smp = bot**.
  Team member names not yet known to a uuid are resolved via Mojang (≤60 HTTP calls
  per derive run; failed lookups cached 7 days in `name_lookups`).
- `api/` (FastAPI, read-only `mode=ro` + `query_only`) serves `/api/stats/*` on
  127.0.0.1:8765 behind nginx. Startup runs `check_required_indexes()`
  (`REQUIRED_INDEXES` in `api/db.py`): the API **refuses to start until
  `bot/snapshot.py` has applied the schema once**.
- Deploy order: pull → `pip install -r requirements.txt` → stop snapshot timer →
  run `bot/snapshot.py` by hand once (migrations + one-time seeding) → start timer →
  install `deploy/systemd/chronos-stats-api.service` → add
  `deploy/nginx/chronos-stats-api.conf` to the nginx site → `nginx -t && reload`.
- Tests live in `tests/` (synthetic fixture DB built via the real snapshot write
  path; no network). Run: `python -m pytest tests -q`.
- Frontend (website Stats pages) is the next step, see `STATS_PLAN.md`.

## Website nav plan (for when Stats ships)
Top-level nav gets one new "Stats ▾" dropdown (Overview / Explorer / Scoreboards /
Player / Compare / Milestones) — NOT six new top-level items, and Bolt map / World
map stay as separate top-level links, not merged into it (different accepted
reasoning: they're not "the same subsystem" the way the six stats views are).
