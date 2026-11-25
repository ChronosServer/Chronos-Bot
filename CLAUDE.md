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
used to pick `dictionaries/dictionary-<version>.json`.

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

## Discord bot layout
- **`main.py`:**
  - Builds a `commands.Bot` and loads every `commands/*.py` as an extension; each file is
    one Cog with `async def setup(bot)`.
  - Starts the client with `await client.start(token)` inside `asyncio.run(main())`.
    Never `client.run()`, and no `nest_asyncio` (removed for Python 3.14).
  - Runs a crash-report watcher task that posts new `crash-reports/` files from every
    server to `bot.crash_report_log_id`.
  - Defines the admin `reload` command.
  - `setup_hook` syncs slash commands to the Chronos guild only (`bot.guild_id`).
- **Commands are hybrid** (`@commands.hybrid_command`): slash, @mention and the `!!` text
  prefix. Each way is toggled in `config.json`: `bot.slash_commands`,
  `bot.mention_prefix`, `bot.text_prefix`. The Message Content intent is only requested
  when the text prefix is on.
- **Permissions:**
  - Admin commands use `@commands.has_permissions(administrator=True)`
    (`ban` uses `ban_members`).
  - Member-only commands (`region`, `structure`, `setdisplay`) check the author's role
    IDs by hand against `bot.member_role`, `trial_member_role` and
    `inactive_member_role`, and reply "Insufficient permission".
- **Conventions:**
  - Every reply is a `discord.Embed` with footer `Chronos™`.
  - Each command file reads `config.json` at import time with a relative path, so run
    the bot from the repo root.
  - `examples/exampleCommand.py` is the template for a new command.
- **`Chronos-Library/`:** shared helpers (`defaultRconLibrary`, `restartLibrary`,
  `statusLibrary`, `tpsLibrary`, `filesizeLibrary`, `confirmLibrary`). Commands import
  them via `sys.path.append('./Chronos-Library/')`.
- **Data sources:**
  - RCON (`server.<key>_rcon_port` + shared `rcon_pass`): execute, whitelist, color,
    online, status, tps, restart.
  - Direct file reads: `stats` reads the SMP `scoreboard.dat`, `players/stats/` and
    `usercache.json`. `storage` copies the region files listed in
    `server.storage_regions` / `dig_storage_regions` into `./region-cache` and scans
    them with `nbt`. `region` and `structure` serve world files.
  - Website files: `webserver` writes to `server.webserver_path`; `syncCommand` and
    `membersExport` write JSON to `<website_path>/data/`.

## commands/syncCommand.py + membersExport.py
Already hardened: every sync step isolated with its own timeout, atomic JSON writes
(`write_json_atomic` → temp file + `os.replace`), loops auto-restart after a crash
instead of dying silently (`tasks.loop` stops permanently on one unhandled
exception — this bit us before, cost ~9 days of stale members data with zero log
output). `/sync_status` shows last-success time and next-run time per loop — check
this first whenever data looks stale, before assuming the sync is broken.

What they write to `<website_path>/data/`:
- `server_status.json`: every 5 min.
- `ms_storage.json` and `dig_storage.json`: every 8 h. An empty scan keeps the old file.
- `archive.json` (forum archive from `bot.archive_channel_id`): every 8 h.
- `members.json` (membersExport): every 1 h.

Loop state is kept in `sync_state.json`. nginx only serves these files by name (see the
`location ~ ^/data/(…)\.json$` whitelist in the site config). A new data file must be
added to that whitelist, or it 404s.

## Stats subsystem (backend on `master` and live since 2026-11-25; website frontend next, in the website repo)
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
The mirror set (live snapshot and import alike) is the **union of every bundled
`dictionaries/dictionary-*.json`** plus the current version's: old-version mirrors still on
the live board (`m-grass_path`) are mirrors too. Current dictionary missing → the bundled
union alone (warn); no dictionary at all → the scoreboard step is skipped with `ok=0`,
never run with an empty mirror set (that would store mirror scores and reset the flags).

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

`stats_tracking` config block (the repo's `config.json` carries every key at its default;
minimal form shown, everything else is optional):
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

**Tuning lives in `stats_tracking` (defaults in `api/settings.py`).** Every tuning value
(per-server `filters_bots`, `scope_groups`, `activity_stat`, milestone rules, badge /
rivalry / spotlight thresholds, Mojang limits, dictionary cache, API presets / limits /
cache) is an optional key there; a missing key = the default in `DEFAULT_CONFIG`, which
is the old hardcoded value. `load_settings()` deep-merges and validates (ValueError naming
the key path; snapshot and API fail loudly, never fall back). Consumers take a `Settings`
object (attribute access): the snapshot builds it each run and passes it to
`run_derive(conn, run_at, mojang, settings)`; the API builds it once via
`api_settings()` from the same config.json as `db_path` (restart to apply). Don't add new
literals to bot/derive*, bot/mojang.py, bot/snapshot.py or api/ — add a key with a
default. Full key list and semantics: `docs/STATS_DATABASE.md` section 1.

The repo's `config.json` has secrets as placeholders. On the live server the owner's
config.json (token, rcon_pass) is never overwritten: only the `stats_tracking` block is
merged in. Validate it with `python -c "import sys,json; sys.path[:0]=['.'];
from api.settings import load_settings; load_settings(json.load(open('config.json'))['stats_tracking'])"`.

Real `config.json` today: `excluded_servers: ["mirror","tour","snapshot","building2"]`;
tracked servers `smp` (group smp), `cmp`, `cmp2`, `cmp3`, `cmp4`, `building` (all group
cmp), all with stats + scoreboards on.

### Verification history (live server, 2026-11-24)
Snapshot runner verified in 3 hand runs: run 1 wrote everything (smp stats 144,592 /
scoreboards 7,697 — the ~126k skipped are vanilla mirrors, 9,223 of 9,346 objectives),
run 2 with no in-game change wrote 0 everywhere, run 3 after real play wrote smp 61 /
13, cmp 30, others 0. Then put on a 15-min systemd timer
(`deploy/systemd/chronos-stats-snapshot.{service,timer}`, installed + enabled on the
server). Live DB: ~3.3k players, ~263k stat_current rows, ~10k scoreboard rows, history
since 2026-11-24. It also has early `ok=1` stats runs with `entries_read=0` (from before
the path fixes) — tracking start must ignore them (see below).

### Deployed (live server, 2026-11-25)
The whole backend runs live (runbook: `TEMP_INFO.md`, untracked):
- First new-code snapshot by hand: every server `ok`, derive `ok`, 26.5 s (incl. totals
  backfill + seeding); steady-state derive ≈ 21 s per 15-min run. Schema v3.
- `chronos-stats-api.service` (uvicorn, 2 workers, `127.0.0.1:8765`) behind the nginx
  `location /api/stats/` block in `/etc/nginx/sites-enabled/default` (one `server {}`,
  port 80, next to `/api/status`). **Cloudflare is in front** (TLS, `server: cloudflare`):
  public URL `https://www.chronosmc.com/api/stats/*`, uncached by Cloudflare (DYNAMIC);
  weak `W/` ETags from Cloudflare are accepted. A Cloudflare rate-limit rule on
  `/api/stats/` (60 req / 10 s per IP) was requested (instructions in `TEMP_INFO.md`).
- curl through nginx: everything 200; uncached ≤ 60 ms except `/player` (~0.46 s, ranks
  every key) and `/search` (~0.23 s); cached 1–7 ms.
- SMP bot rule on live data: 120 counted of 2,687 flagged uuids (2,567 bots).
- Backup import done: 5 SMP backups (1.16.5 2021-10-28, 1.17.1 2023-12-16, 1.18.2
  2024-01-26, 1.20.2 2024-08-25, 1.21 2026-07-15; dates owner-confirmed), 210,505 stat +
  8,000 scoreboard rows, rebuild 5.8 s; undo copy `pre-import-20261125-074152.db`.
  `tracking_started_at:smp` = 2021-10-28; 2,897 approximate firsts, 28,866 approximate
  milestones, 122 Firsts badges. 16b (extra rebuild) not needed.
- Tests: 358, all passing locally.

### What the backend is (merged to `master` 2026-11-25)
Pipeline: timer → `bot/snapshot.py` (history, change-only) → `bot/derive.py` (derived
tables) → SQLite → `api/` (FastAPI, read-only) → nginx `/api/stats/*` → website.

| Path | Role |
|---|---|
| `bot/snapshot.py` | reads stat files + scoreboard.dat, writes change-only history; `run_once(config, now_fn, dictionary, mojang_fetch)` — injectable for tests; one run-wide `now` shared by every server |
| `bot/mojang.py` | `MojangClient`: bulk name→uuid + uuid→name, limits from `stats_tracking.mojang` (defaults ≤60 calls, 3 s/call, 60 s wall cap per run, results incl. failures cached 7 days in `name_lookups`); only valid MC usernames are sent |
| `bot/derive.py` (+ `derive_common.py`, `derive_achievements.py`, `derive_social.py`) | stages in order: activity → totals (incl. one-time history backfill) → firsts → milestones → rivalries → badges → spotlight → resolve_names → write_meta (always). Failure = `snapshot_runs.kind='derive' ok=0`, never fails the snapshot |
| `api/settings.py` | **the only place tuning values live**: defaults, deep merge, validation of `stats_tracking`; `load_settings()`, `DEFAULTS`, `api_settings()` |
| `api/scope.py` | **the only place participant rules live** (scopes, SMP bot rule, fake/unresolved, mirrors) as SQL fragments; derive imports it too |
| `api/timeq.py` | ranges (presets from `api.range_presets`, default `1d/1w/1m/6m/1y`, plus `all`; from/to), clamp to tracking start, value-at-T reads, downsampling |
| `api/history.py` | gap snapping: history points per server (meta), `snap_from` — a range starting before live tracking starts at the next real observation |
| `api/cache.py` | `@cached`: key = path + declared params + data_version; ETag (includes a settings fingerprint)/304; LRU bounds from `api.cache_*` (default ≤128 MiB, bodies >8 MiB not cached) |
| `api/db.py` | read-only connections; `data_version` = live `MAX(snapshot_runs.id)`; `REQUIRED_INDEXES` startup check |
| `api/routes/*` | meta, leaderboard, series/totals, player/compare, search/keys, feeds (milestones, firsts, rivalries, activity, spotlight) |
| `sql/schema.sql`, `sql/migrations.py` | schema (CREATE IF NOT EXISTS) then migrations by `PRAGMA user_version` (now 4: v3 adds `approximate` on firsts/milestones and the `imports` table; v4 adds `servers.position`) |
| `tools/` | `import_backups.py` (old world backups into history), `stat_renames.json` (old->new stat keys, editable), `README.md` (operator guide) |
| `dictionaries/` | bundled every-scoreboard `dictionary-<version>.json`, 1.16-26.2 (34 files; 1.19.1/.3/.4 missing); mirror-objective lists for the snapshot and the import |
| `deploy/` | systemd units (snapshot, timer, api) + nginx snippet; `update.sh` (git-pull updater, see Deploy) + `check_config.py` |
| `bot/merge_players.py`, `api/merges.py`, `player_merges.json` | account merges (alt → main): the one-time fold, the parser, the owner's list |
| `bot/history_fill.py` | the 0 rows a server's first live run implies (keys only in backups) |
| `tests/` | pytest, 404 tests, synthetic worlds built through the real snapshot write path, no network; tests never read the repo's config.json or player_merges.json (autouse fixtures; `api_config` writes a config) |
| `docs/STATS_PLAN.md` | agreed scope, hard rules, build order/status |
| `docs/STATS_API.md` | the HTTP contract for the website (copy into the website repo) |
| `docs/STATS_DATABASE.md` | operator guide: config, first setup, upgrade, health checks, backup, reset |
| `docs/superpowers/specs/2026-11-24-stats-api-design.md` | approved backend design incl. Addendum A (badges/rivalries/spotlight rules) |

### Rules and decisions that bind future work (don't relitigate)
- **SMP bots, strict:** not in a team = bot; **missing `player_flags` row on smp = bot**
  (fail closed). Owner-confirmed on live data: ~196 team members; 968 of 1,087 SMP
  stat-file holders are bots (`BedBot`, `Endswitch`, `digsort`, `001`…) — these are named
  automation accounts WITH stat files, a different thing from the fake-player NPCs.
  Flags are written only for uuids with an SMP stat file. Bot filtering keys on
  `servers.filters_bots=1`, set from config `servers.<key>.filters_bots` (default: smp
  only — keep it that way, it is the owner's rule). CMPs never filter.
- **Scopes:** `all`, each `scope_groups` name (default `cmps` → group_key `cmp`), or one
  server key (`smp`, `cmp2`, …); only
  `provisioned=1` servers (the `all` pseudo row in `servers` is provisioned=0 and exists
  only as an FK target for `snapshot_runs.kind='derive'`). Multi-server scopes sum per
  player. A bot on SMP still counts on CMPs.
- **usercache.json has one entry per NAME**, so a renamed player has one per name used;
  `expiresOn` (last login + 1 month) says which is newest. Always take the newest entry
  per uuid (`snapshot.load_usercache` / `newest_names`; `statsCommand.load_usercache`),
  never "last in file" (that put `NotQuiteRat_` back as the live name). `players.name`
  (display) is the newest across **all** servers' usercaches; participant resolution
  stays per server (newest name only). The SMP team check counts a uuid as teamed if
  **any** of its usercache names is in a team (team lists hold names). The backup import
  learns every entry's name (old ones too) for merging.
- **Account merges (`player_merges.json`, repo root; owner's list).** Separate accounts
  counted as one player (first entry: LordHamster42 → hampter42, different uuids). Not
  for renames: a rename is the same uuid and needs nothing. Entries by uuid + the
  from-account's scoreboard `names` (scoreboards key by name; SMP's usercache may not know
  the alt). `api/merges.py` parses it (chains resolve, bad file raises). The snapshot's
  first step (`bot/merge_players.apply_pending_merges`, before any server is read) folds a
  NEW entry's existing data in once: `pre-merge-<stamp>.db` backup, then per (server, key)
  A's and B's change-only timelines → one timeline of their sum under B; current B += A;
  firsts/milestones (earlier kept)/badges/activity → B; totals rebuilt; meta
  `merged:<A>`. It must run before the first live run that sums them (else A counted
  twice), hence inside the snapshot. From then on the live snapshot and the import sum a
  merge group's stat files / scoreboard entries under B; the API's `resolve_player` maps A
  (uuid or listed name) to B. Removing an entry doesn't split data (restore the backup).
  Not backfilled: milestones only the sum crossed before the merge. `update.sh` treats a
  `player_merges.json` change as a stats + API change.
- **Server order = config order.** `sync_servers_table` (`bot/snapshot.py`, every run)
  writes `servers.position` = index in `stats_tracking.servers`; servers no longer in
  config get NULL (last, by key). Everything that lists servers sorts by
  `api.scope.SERVER_ORDER_SQL` (or `order_servers()` for a key set): `/meta.servers`,
  `scopes`, `scope_groups`, scope tuples, `/player.servers`, `/search` objective servers,
  derive's server loop. Never `ORDER BY key` on servers. A reorder in config takes effect at
  the next snapshot run (new `data_version` → new ETags), no API restart.
- **Tracking start** per server = `MIN(at)` of stats **and import** runs with `ok=1 AND entries_read>0`.
  Ranges reaching earlier are clamped and responses carry `effective_from`. After a backup
  import it moves back to the oldest backup, so it (and `effective_from`) can be years
  before 2026-11-24 (live tracking start). Never hardcode that date in docs or the website.
- **Gap snapping** (`api/history.py`, the one place the rule lives): before a server's
  first live run L, history exists only at import points. A range start `from_ts < L`
  becomes the smallest point ≥ `from_ts` among {import points, L} (the next real
  observation, never a stale earlier backup); `from_ts ≥ L` is unchanged (dense);
  before the first point it's the tracking-start clamp's job. Multi-server scopes take the
  max of the servers' snapped starts. derive writes the points to meta
  (`history_points:<server>` JSON list = imports + L, `live_started_at:<server>`);
  `timeq.parse_range` and the rivalry window (`api/rivals.window_floor`) read them, and
  `derive_common.clamped_ago` (spotlight mover baseline, rivalry `gap_7d_ago`) computes
  them from `snapshot_runs`. Servers without imports: points = [L], i.e. as before.
- **Firsts/milestones that predate tracking are `earliest_known=1`** ("earliest known
  holder" / "already had"), never given a fake date. Seeding happens once (meta
  `seeded_achievements`) and on each new server's first real run. Seeded firsts don't
  count toward Firsts badges.
- Badges (36), rivalries (gap ≤2%, leader ≥1000, both active 7d, gap moving, ≤5/player,
  ≤3/key) and spotlight (outlier/rare/recent_milestone/recent_first/mover) are specified
  exactly in the spec's Addendum A — implement changes there first. The numbers are the
  `stats_tracking` defaults; changing a number is a config edit, not a code change.
  Milestone rules are synced each run (thresholds updated, removed rules `enabled=0`,
  never deleted, no backfill); a changed badge threshold changes the badge key and
  awarded badges stay.
- **Totals** are fully recomputed every run (set-based) and history was backfilled once
  (meta `totals_backfilled`); `total_no_bots` uses today's bot flags for all history.
  So a reclassification must rebuild it: each run stores every bot-filtering server's
  sorted counted-uuid set (meta `counted_set:<server>`, JSON, one query per server).
  `check_counted` rebuilds only that server's totals history (`rebuild_totals`, same
  transaction, no step at the flip run), and only when a uuid that flipped has history
  before `run_at` (a brand-new team member changes nothing earlier). First run: just
  stored. Legacy `counted_fp:<server>` sha1 (no diff possible): a mismatch rebuilds that
  server, then the key is replaced by the set. A missing `totals_backfilled` still
  rebuilds every server.
- **Performance gotcha:** without `ANALYZE` SQLite picks the `(server_key, at)` indexes
  and scans whole history. Hot queries are pinned with `INDEXED BY`; every pinned index
  must be listed in `api/db.py REQUIRED_INDEXES` (the API refuses to start if one is
  missing) and get an `EXPLAIN QUERY PLAN` test. New indexes on columns added by a
  migration go in `sql/migrations.py`, not schema.sql (schema.sql runs first).
- Routes are sync `def h(request: Request, conn=Depends(db.get_db), ...)` under
  `@cached` (decoration fails without `request`+`conn`); `/search` and `/spotlight` are
  deliberately not cached.

### Backup import (`tools/`)
- `tools/import_backups.py` (guide: `tools/README.md`) imports old SMP world backups as
  earlier change-only history points (`snapshot_runs.kind='import'`, `imports` table);
  never writes `stat_current`/`scoreboard_current`. Old keys are mapped through
  `tools/stat_renames.json` (editable data).
- `dictionaries/` bundles every-scoreboard's `dictionary-<version>.json` for 1.16–26.2
  (not 1.19.1/.3/.4). The import and the live snapshot skip the mirror names of all of
  them (dry run should show ~0 "probable mirrors"), and `fetch_dictionary` prefers the
  local file over GitHub. `commands/statsCommand.py` falls back to the newest bundled
  dictionary when `minecraft_version` has none (console warning).
- Run it on the server by hand with the timer + service stopped (it gives up after 5 s on
  a busy DB), after at least one live snapshot exists; `--dry-run` first. It makes
  `pre-import-<UTC stamp>.db` next to the DB before writing (undo = restore it). Refuses
  backups dated on/after the first live snapshot. `--rebuild-only` reruns just the rebuild.
  No activity history is imported (heatmap/pulse/active badges start at deploy).
- **`approximate=1`** on firsts/milestones: crossed between two points (backup→backup or
  last backup→first live run), dated at the later point, shown as "by <date>". Distinct
  from `earliest_known=1` (already true at the oldest point, no date). Approximate firsts
  count for Firsts badges; earliest-known ones don't.
- `rebuild_from_points` (`bot/derive_achievements.py`) is the **only** writer of
  approximate rows. It reuses the live firsts/crossing INSERTs, fed by value-at-T
  sources, and keeps real live rows (OR IGNORE). It also re-runs the totals backfill
  and badges. It takes its own `BEGIN IMMEDIATE` lock and runs as one transaction.
- Totals backfill treats the first live run (per table) as a full observation: a
  (participant, key) with history but absent from `stat_current`/`scoreboard_current`
  (backup-only) counts 0 from then on, so backfilled totals match the live recompute. This
  is a no-op without imports. **Per-player history matches it** (`bot/history_fill.py`,
  fixed 2026-11-25 after the website found negative delta leaderboards): a key that is
  **absent from the current table** and whose latest (non-zero) row is before the server's
  first live run L (per table) gets a real `(L, 0)` row, so "latest row ≤ T" never
  carries a stale backup value past L. Never key it on "no row at L": on the live DB some
  servers (cmp, partly smp) got full rows from runs before the first counted one, so
  unchanged keys have no row at L and must not be zeroed (the first version did that;
  repaired by restoring `pre-merge-20261125-164507.db` and deleting those rows). Written once for an existing DB by the
  snapshot (meta `live_start_filled`), and by every import / `--reimport-scoreboards`
  for its server. Plus: delta leaderboards and `/compare` clamp **stat** deltas at 0
  (vanilla stats only count up; a drop = a reset or lost file); scoreboard deltas keep
  their sign (objectives can legitimately go down).

### Deploy / redeploy (done 2026-11-25 — runbook in `TEMP_INFO.md`; this is the order for any redeploy)
Order, on the live server (Debian, root, repo `/opt/chronos/Chronos-Bot`, Python
`/opt/python/bin/python3`):
1. Get the code there: the owner normally copies the changed files with WinSCP (never
   overwriting the server's `config.json` or anything in `/opt/chronos/stats/`); `git pull`
   works too but a locally modified tracked `config.json` blocks it (stash/re-apply).
2. Merge ONLY the `stats_tracking` block into the server's `config.json` and validate it
   with `load_settings` (see above).
3. Install fastapi, uvicorn[standard], orjson (pins in requirements.txt). `pip install -r
   requirements.txt` is fine now that nest_asyncio is gone from it, but its `==` pins move
   the bot's installed packages to exactly those versions: `--dry-run` it first.
4. Stop the snapshot timer + in-flight service, run `bot/snapshot.py` once by hand
   (migrations to v3, totals backfill, seeding, about 20-60 s), start the timer.
5. Install + start `chronos-stats-api.service`; add the nginx block inside the existing
   `server {}` next to `/api/status`; `nginx -t && reload`; curl checks.
6. Backup import: stop timer + service, `tools/import_backups.py --server smp --dry-run`
   on the SMP backups (1.16/1.17/1.18/1.20/1.21), then the real run, then start the timer.
   The API can keep running. Then check `/meta.tracking_started_at.smp` moved back.
7. The code is on `master` (origin). **Later updates: `bash deploy/update.sh`** (as root,
   in the repo; `--check` first shows incoming commits + the config check and changes
   nothing). It replaces WinSCP copying and steps 1-5 for routine updates:
   - fetch, fast-forward only; refuses on server-only commits or tracked files that differ
     from both git and the incoming version (`--discard-local` backs them up to
     `/opt/chronos/config-backups/local-files.<stamp>/` and takes git's). Files already
     identical to the incoming version (copied by hand) are fine.
   - `config.json` stays tracked (owner's choice) but is **never written**: backed up to
     `/opt/chronos/config-backups/config.json.<stamp>`, set aside for the merge, copied back
     byte for byte (cmp-checked). `deploy/check_config.py` compares it with the incoming
     repo `config.json` (MISSING keys = warn, add by hand; stats_tracking left-out keys =
     defaults; invalid stats_tracking per the *incoming* `load_settings` = stop before
     changing anything).
   - Server checkout is sparse (`SPARSE_PATTERNS` in the script: no tests/, docs/,
     examples/, planning .md files); classic `.git/info/sparse-checkout` + `read-tree`
     (works on every git version; `sparse-checkout set --no-cone` differs between them).
     Server git is 2.55. The repo files there aren't owned by root, so root needs
     `git config --global --add safe.directory /opt/chronos/Chronos-Bot` once (git's
     "dubious ownership" refusal).
   - Stats paths changed (bot/ sql/ api/ dictionaries/): stop timer, wait for a running
     snapshot, update, one snapshot by hand (migrations), restart the API if api/ or sql/
     changed and curl it, start the timer. requirements.txt: pip dry-run, then asks.
     Bot code (main.py, commands/, Chronos-Library/): offers to kill `python3 main.py`
     (start.sh relaunches it 5 s later; the bot runs via start.sh in screen/tmux).
     systemd/nginx files: prints what to copy, never installs.
   - Logs to `/opt/chronos/update-logs/update-<stamp>.log` (owner downloads it).
   The whole script body is one function, so a pull that replaces the script is safe.
   Paths are env-overridable (`CHRONOS_PY`, `CHRONOS_LOG_DIR`, ...) for local testing.

### Working with the owner
The owner can't copy/paste reliably from the server terminal: put server commands in
`TEMP_INFO.md` (untracked scratch, never commit) and have them paste output at the
bottom. No `sqlite3` CLI on the server — use `python3 -c "import sqlite3..."`.
Local tests: `pip install -r requirements.txt -r requirements-dev.txt`
then `python -m pytest tests -q` (Windows, Python 3.13 locally; server is 3.14).

## Resolved follow-ups (2026-11-25)
1. **Custom range entirely inside the pre-live gap** (`Range.in_gap` / `for_values()` in
   `api/timeq.py`): `/series`, `/totals` and compare series return one point, the value at
   `to`; leaderboard/compare deltas stay empty ("no observation in the window").
2. **Bot reclassification** rebuilds only the changed server, only when a flipped uuid has
   earlier history (see Totals above).
3. **Mirror objectives flagged after the fact**: their scoreboard firsts/milestones
   (`api.scope.achievement_not_mirror_sql`: feeds, player, badges, spotlight via
   `derive_common.counted_achievements`) and `/totals?kind=scoreboard`
   (`objective_not_mirror_sql`) are hidden at read time, never deleted. Prevention still
   applies: bundle the new version's dictionary (every-scoreboard `create.py`) before
   bumping `server.minecraft_version`.

4. **Tracking toggle off hides that server's data** (`api.scope.tracked_sql`; fixed
   2026-11-25, not deployed yet). Read time only, never deleted: `stat_filter_sql` /
   `scoreboard_filter_sql` add an `EXISTS (servers ... track_*=1)` clause by default
   (`track="stats"` / `"scoreboards"`; `track="category"` for rows with a category column:
   firsts, milestones, spotlight; `scoreboard` → `track_scoreboards`, else `track_stats`).
   Also `/search` objectives, `/keys` scoreboard, `/totals`, `/player` servers/firsts/
   milestones, `/meta.categories`. **Derive passes `track=None`** and keeps computing on the
   frozen rows: hiding there would write a fake drop to 0 into totals history (and a jump
   back when re-enabled). Badges already awarded stay visible. A toggle change takes effect
   at the next snapshot run (it syncs `servers`). Owner can now turn `track_scoreboards`
   off on the CMPs + `building` once this is deployed.
5. **Import: renamed players' old-name scoreboard entries** (fixed 2026-11-25; owner's
   decision: **match by uuid, sum under the latest name**). In `_scoreboard_rows`, every raw
   name that resolves to a uuid (any backup's usercache, or the live `players` names) is
   summed per (uuid, objective) into one row whose raw_name is the uuid's latest name (live
   name, else newest backup name). Same result as the in-game `stattransfer <old> <new>`
   (`+=` then reset), so history doesn't jump at the transfer, and never two rows for one
   (uuid, objective, at). Generic, nothing name-specific (owner: NotQuiteRat_ →
   NotQuiteRat, LordHamster42 → hampter42, Obi81 → ObiWan8_, ...). The import and the repair
   print the `old -> current` pairs they merged. Limit: an old name in no backup usercache
   and not a live name can't be tied to a uuid (Mojang has no name-history API since
   2022, and name→uuid returns today's holder, possibly someone else); it stays
   `unresolved:`. Live DB repair: `import_backups.py --server smp --reimport-scoreboards`
   (dry run first; rewrites the scoreboard rows at every recorded import point from its
   folder, adjusts `rows_written`, then rebuilds; needs the backup folders).
6. **Live snapshot records a reset score as 0** (fixed 2026-11-25). `run_scoreboard_snapshot`:
   when the `PlayerScores` tag is present, an entry in `scoreboard_current` whose objective
   is still on the board (and not a mirror) but which is gone this run gets a value-0
   history row + current 0 (raw_name from its latest history row). Missing tag = nothing;
   removed objective = `scoreboard_meta.removed`. A reset player then shows 0 (like any
   0 score), not their stale value.

## Known follow-ups (open)
None from the stats backend. Bot-side requests queued by the owner (2026-11-25): the
members export should use each member's Discord username, not their Chronos server
nickname; and archive pictures should be cached (they disappear after a while; Discord
attachment URLs expire).

## Website nav plan (for when Stats ships)
Top-level nav gets one new "Stats ▾" dropdown (Overview / Explorer / Scoreboards /
Player / Compare / Milestones) — NOT six new top-level items, and Bolt map / World
map stay as separate top-level links, not merged into it (different accepted
reasoning: they're not "the same subsystem" the way the six stats views are).
