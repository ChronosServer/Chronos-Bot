# Chronos Stats API: contract for the website

Copy this file into the website repo next to `STATS_PLAN.md`. It describes every
endpoint the Stats pages can use, exactly as the backend (Chronos-Bot, branch
`stats-api`) implements it. `STATS_PLAN.md` is the scope and feature list; this file is
the data contract.

## Basics

- Base URL: same origin as the site, `/api/stats/…` (nginx proxies it to the API). No
  auth and no CORS needed. All endpoints are `GET` and return JSON.
- **The data changes only every 15 minutes**, when a snapshot runs. Responses carry
  `ETag` and `Cache-Control: public, max-age=60, stale-while-revalidate=900`, so let the
  browser cache work. **Don't add cache-buster query params**; unknown params are
  ignored anyway.
- Timestamps are **unix seconds (UTC)**. Values are raw Minecraft integers (see Units).
- Errors:
  - `400 {"error": "..."}`: bad scope, range, kind or limits.
  - `404 {"error": "player not found"}`.
  - `422 {"detail": [...]}`: a param has the wrong type or is out of range, e.g. a
    non-integer `from`.
- Player heads: `https://crafatar.com/avatars/<uuid>?size=32&overlay` (works for every
  uuid, named or not).
- `name` can be `null`: about 1.6k old players have no known name yet (lookups fill
  them in slowly). Show the first 8 characters of the uuid plus the head instead.

### Scopes and bots (apply on every page)
- `scope` is one of `all`, `smp`, `cmps`, or a single server key such as `cmp2` or
  `building`. `/meta.scopes` lists the valid ones (group scopes like `cmps` are set in
  the backend config, so take the list from there rather than hardcoding it). The UI needs **All / CMPs / SMP**
  as the main switch, plus a picker for single servers.
- `include_bots` (default `false`) only affects SMP: on SMP, players in no team are bots
  and are hidden by default. CMPs always show everyone. Offer an "include bots" toggle
  only where SMP data is in view.
- Multi-server scopes **sum** a player's values across servers.

### Time ranges
`range=1d|1w|1m|6m|1y|all`, or `from=<unix>&to=<unix>` (both optional). The presets
are backend config: build the range picker from `/meta.settings.range_presets`
(name → seconds) plus `all`, which is always valid.
- No `range`/`from` → **current totals** (`mode: "total"`). With `to` only → totals as of `to`.
- With `range` or `from` → **the gain over that window** (`mode: "delta"`).
- History starts at the server's **tracking start**, `/meta.tracking_started_at[server]`.
  Live tracking began on 2026-11-24, but once old world backups are imported
  (`tools/import_backups.py`) the tracking start moves back to the oldest backup, so
  `tracking_started_at` and `effective_from` can be **years earlier** (for example 2021). Never hardcode the 24 Nov 2026 date.
- Windows that reach further back than the tracking start are clamped, and the response's
  **`effective_from`** says the real start. Show "since tracking began (<date>)", with
  the date formatted from `effective_from` (or `tracking_started_at`), whenever
  `effective_from` > the requested start.
- **`effective_from` may also snap forward to the next data point before live
  tracking.** Before a server's first live snapshot its history exists only at imported
  backups, months apart. A window starting in that sparse span is measured from the next
  real observation (the next backup, or the first live snapshot), never from an older
  backup, so "last week" right after the import is the gain since the first live
  snapshot, not years of play. Example: backups 2021-02 and 2021-09, live since
  2026-11-24; `range=1w` on 2026-11-28 gives `effective_from` = 2026-11-24, and
  `from=2021-05-01` gives `effective_from` = the 2021-09 backup. A multi-server scope
  uses the latest of its servers' snapped starts. Windows starting after the first live
  snapshot are never moved. Show the same "since <date>" note whenever
  `effective_from` > the requested start.
- A window that ends before tracking began returns empty rows.
- Between backups (and between the last backup and the first live snapshot) history has
  only a few points, so graphs over imported periods are coarse step lines.

### Units (raw values → display)
- `minecraft:custom` keys ending in `_one_cm` are **centimetres**: /100 = m, /100000 = km.
- `minecraft:play_time`, `minecraft:total_world_time`, `minecraft:time_since_death`,
  `minecraft:time_since_rest` and `minecraft:sneak_time` are **ticks**: /20 = s,
  /72000 = h.
- `minecraft:damage_*` keys are in **tenths of a heart-point**: /10.
- Everything else is a plain count. Format big numbers compactly (1.2M) with the
  exact value in a tooltip.
- Stat keys look like `category=minecraft:mined`, `key=minecraft:stone`. Display them
  without the namespace and with spaces ("Mined · Stone"). For item icons use whatever
  the site already uses (the site has `/cache/items/*.png`).

---

## Endpoints

### `GET /meta`
Load this once per page.
```json
{"data_version": "4127", "last_run_at": 1795600000,
 "servers": [{"key":"cmp","label":"CMP","group":"cmp","filters_bots":false,
              "track_stats":true,"track_scoreboards":true}, …],
 "scopes": ["all","smp","cmps","building","cmp","cmp2","cmp3","cmp4"],
 "categories": ["minecraft:broken","minecraft:crafted","minecraft:custom", …],
 "tracking_started_at": {"smp": 1795527876, …},
 "settings": {…}}
```
- Use `last_run_at` for an "updated N min ago" label.
- `tracking_started_at`: per server key, unix seconds of the oldest history point
  (the oldest imported backup if there is one, else the first live snapshot). Servers
  with no history yet are missing from the map.
- `filters_bots: true` marks the server where the bot toggle matters (smp).
- `scopes` is in menu order: `all`, single servers outside any group (`smp`), group
  scopes (`cmps`), then the servers inside groups.

#### `/meta.settings`
The backend's tuning values. They come from the server's config, so **read presets,
limits and badge thresholds from here instead of hardcoding them**:
```json
"settings": {
  "range_presets": {"1d": 86400, "1w": 604800, "1m": 2592000, "6m": 15724800, "1y": 31536000},
  "activity_bucket_s": 900,
  "limits": {"leaderboard": 200, "feed": 200, "search": 50, "series_players": 6,
             "series_points": 500, "player_milestones": 50},
  "compare": {"min_players": 2, "max_players": 6, "max_keys": 20},
  "badges": {"firsts": [1, 10, 50], "top1": [1, 10, 50],
             "big_numbers": [1000000, 10000000],
             "category_tiers": [100000, 1000000, 10000000],
             "categories": ["minecraft:mined", "minecraft:used", …],
             "veteran_hours": 1000,
             "regular": {"days": 20, "window_days": 30},
             "night_owl": {"min_buckets": 40, "window_days": 30, "share": 0.5,
                           "start_hour": 0, "end_hour": 6}}
}
```
(the values shown are the defaults)
- `range_presets`: the valid `range=` values besides `all`.
- `limits`: the most a request returns. `leaderboard` caps `/leaderboard` `limit`, `feed`
  caps `/milestones` and `/rivalries`, `search` caps `/search`, `series_players` is the
  most uuids `/series` takes, `series_points` the most points per line,
  `player_milestones` how many milestones `/player` returns. A larger `limit` is clamped,
  not rejected.
- `compare`: how many players (min/max) and keys `/compare` accepts.
- `badges`: the thresholds behind each badge key, for "Pioneer: 10 real firsts" style
  descriptions and progress bars. Badge **display names stay in the frontend**, keyed by
  family and tier order (the first `firsts` tier is Trailblazer, the second Pioneer, the
  third Founder; `cat:<category>:1/2/3` are I/II/III, and so on).

### `GET /search?q=sto&limit=10`
Autocomplete. It answers from memory, so call it on every keystroke (debounce about 120 ms).
```json
{"players": [{"uuid":"…","name":"Stonks"}],
 "stats": [{"category":"minecraft:mined","key":"minecraft:stone"}, …],
 "objectives": [{"objective":"digs","servers":["smp","cmp"]}]}
```
- Matching is case-insensitive. Prefix matches come first, then shorter ones.
- `stone` matches `minecraft:stone`. The same key appears once per category (mined,
  used, crafted…), so group them in the dropdown.
- `limit` defaults to 10, max `settings.limits.search` (50).

### `GET /keys?scope=all&kind=stat|scoreboard`
Every stat or objective present in the scope, for browse menus.
- `kind=stat` → `{"stats": {"minecraft:mined": ["minecraft:stone", …], …}}`
- `kind=scoreboard` → `{"objectives": [{"objective":"digs","removed":false}, …]}`.
  Vanilla-copy objectives are already excluded. `removed: true` objectives still have
  history, so mark them as removed.

### `GET /leaderboard`
Params:
- `kind=stat|scoreboard`
- `category` and `key` for stats; for scoreboards only `key=<objective>`
- `scope`
- `range` or `from`/`to`
- `include_bots`
- `limit` (default 50, max `settings.limits.leaderboard`, 200) and `offset`

```json
{"effective_from": 1795527876, "to": 1795600000, "mode": "total",
 "total_count": 1087,
 "rows": [{"rank":1,"uuid":"…","name":"PapiChase","value":17327}, …]}
```
- Ties share a rank (1, 1, 3).
- In delta mode, players with a gain of 0 are left out.

### `GET /series`
Graph lines for 1–6 players (`settings.limits.series_players`). Params: `kind`, `category`, `key`, `uuids=a,b,c`,
`scope`, `range` or `from`/`to`, `include_bots`.
```json
{"effective_from": …, "to": …,
 "series": {"<uuid>": [[ts, value], [ts, value], …], …}}
```
- Values are **cumulative totals** in step form. The first point is at
  `effective_from`, and each later point is a change.
- Draw as a step line (`curveStepAfter`) and extend the last value to `to`.
- At most 500 points per line (`settings.limits.series_points`). A uuid with no data
  returns `[]`.

### `GET /totals`
Server-vs-server comparison: whole-server total lines. Params: `kind`, `category`,
`key`, `scopes=smp,cmps`, `range` or `from`/`to`, `include_bots`.
```json
{"effective_from": …, "to": …, "series": {"smp": [[ts,total],…], "cmps": [[ts,total],…]}}
```
- Same step format as `/series`, and all lines share the same `effective_from`.

### `GET /player/{uuid-or-name}`
The player profile ("dossier"). Params: `scope`, `include_bots`.
```json
{"uuid":"…","name":"Chezloc","name_resolved":1,"last_activity":1795599000,
 "is_bot_on_smp": false, "servers": ["cmp","smp"],
 "stats": [{"category":"minecraft:mined","key":"minecraft:stone","value":123456,"rank":4,"of":812}, …],
 "scoreboards": [{"objective":"digs","value":5000,"rank":2,"of":40}, …],
 "badges": [{"badge_key":"firsts:1","server_key":"smp","awarded_at":…}, …],
 "milestones": [{"at":…,"server_key":"smp","rule_key":"cat:minecraft:mined",
                 "category":"minecraft:mined","stat_key":"minecraft:stone",
                 "threshold":100000,"value_at":100342,"earliest_known":0,"approximate":0}, …],
 "firsts": [{"at":…,"server_key":"smp","category":"minecraft:crafted",
             "stat_key":"minecraft:crafter","earliest_known":0,"approximate":0}, …],
 "rivals": [{"server_key":"smp","category":"minecraft:crafted","stat_key":"minecraft:crafting_table",
             "uuid_a":"…","uuid_b":"…","other_uuid":"…","other_name":"PapiChase",
             "gap":339,"gap_7d_ago":800,"computed_at":…,
             "closing_per_day":65.8,"overtake_days":5.2}, …]}
```
- A name lookup is case-insensitive.
- `milestones` holds the latest 50 entries (`settings.limits.player_milestones`).
- "Best ranks" = sort `stats` by `rank / of`.
- If `is_bot_on_smp` is true, the player's own numbers still show. Label them as a bot
  account.

### `GET /compare`
N-way compare. Params:
- `uuids=` 2–6 names or uuids (`settings.compare.min_players` / `max_players`)
- `keys=minecraft:mined|minecraft:stone,scoreboard|digs`: up to 20 keys
  (`settings.compare.max_keys`); each key is
  `category|key` for stats or `scoreboard|<objective>`
- `scope`, `range` or `from`/`to`, `include_bots`

```json
{"players": [{"uuid":"…","name":"…"}, …], "effective_from": …, "to": …,
 "values": {"minecraft:mined|minecraft:stone": {"<uuid>": 123, …}, …},
 "series": {"minecraft:mined|minecraft:stone": {"<uuid>": [[ts,v],…]}, …}}
```
- An SMP bot shows 0 unless `include_bots=true`. If a compared player has
  `is_bot_on_smp`, turn the bot toggle on for the comparison.

### `GET /milestones?scope=all&limit=50&before=<cursor>`
The milestones feed. `limit` max `settings.limits.feed` (200).
```json
{"rows": [{"id":…,"at":…,"server_key":"smp","uuid":"…","name":"…",
           "category":"minecraft:mined","key":"minecraft:stone",
           "threshold":1000000,"earliest_known":0,"approximate":0}, …],
 "next": "0:1795599000:812"}
```
- Pass `next` back as `before` for the next page; `null` means the end.
- Real crossings (`earliest_known: 0`) come first, newest first. Then the "already had
  before tracking" ones (`earliest_known: 1`). Show those under a separate, quieter
  heading such as "Already reached before tracking began", without a date.

### `GET /firsts?scope=all`
`{"rows": [{"server_key","category","key","uuid","name","at","earliest_known","approximate"}, …]}`
- `earliest_known: 1` → label it **"earliest known holder"**, not "first ever".
- `earliest_known: 0` → an observed first, so show the date (but see `approximate`).
- `approximate: 1` (on firsts and milestones, in `/firsts`, `/milestones` and `/player`)
  → the thing happened *between two history points* (backup to backup, or the last
  backup to the first live snapshot) and `at` is the **later** point. So it is only known
  to have happened **by** that date: show "by <date>", not an exact date. It is a
  different thing from `earliest_known: 1` (already true at the oldest point, no date).
  Approximate firsts and milestones sort and count like dated ones. `approximate` is
  always `0` until a backup has been imported.

### `GET /rivalries?scope=all&uuid=<optional>&limit=50`
`limit` max `settings.limits.feed` (200).
```json
{"rows": [{"server_key":"smp","category":"…","key":"…",
           "a":{"uuid":"…","name":"…"}, "b":{"uuid":"…","name":"…"},
           "gap":339,"gap_7d_ago":800,"closing_per_day":65.8,"overtake_days":5.2}, …]}
```
- `a` is currently ahead of `b`.
- Rows are sorted soonest-overtake first.
- `closing_per_day > 0` means b is catching up; negative means the gap is widening.
- `overtake_days: null` means b isn't catching up.

### `GET /activity?scope=smp&range=1m`
`{"bucket_seconds": 900, "buckets": [[ts, active_players], …]}`
- Each bucket is `bucket_seconds` long (15 minutes by default, same as
  `settings.activity_bucket_s`; UTC). Empty buckets are left out, so fill gaps with 0.
- Bin them in the **viewer's local time** to build:
  - the **activity heatmap** (day of week × hour);
  - the **server pulse clock** (24-hour radial: average players per hour of day).
- Activity history starts at deploy time (never from imported backups), so these fill in
  over the first weeks. Show an empty state until then. `include_bots` applies here too.

### `GET /spotlight?scope=all&seen=<cat|key,…>`
A random notable fact; it is never cached. Keep the last 10 shown `category|key` pairs
in `sessionStorage` and send them as `seen` to avoid repeats.
```json
{"trigger":"outlier","server_key":"smp","uuid":"…","name":"Placer",
 "category":"minecraft:mined","key":"minecraft:obsidian","value":48000,
 "detail":{"median":1200,"ratio":40.0,"holders":312}}
```
`{"trigger": null}` → hide the widget. `recent_milestone` / `recent_first` never pick an
imported (`approximate`) row: those are dated at a backup, not when they happened.
Sentence templates per trigger:

| trigger | detail | sentence |
|---|---|---|
| `outlier` | `median`, `ratio`, `holders` | "{name} has {verb} {ratio}× more {thing} than the average player" |
| `rare` | `holders` | "Only {holders} players have ever {verb} {thing}. {name} has {value}." |
| `recent_milestone` | `threshold`, `rule_key`, `at`, `approximate` (always 0) | "{name} passed {threshold} {thing} {verb} {relative time}" |
| `recent_first` | `at`, `approximate` (always 0) | "{name} was the first on {server} to {verb} {thing}" |
| `mover` | `from`, `delta` | "{name} went from {from} to {value} {thing} {verb} this week" |

Verb per category:
- mined → "mined"
- used → "used/placed"
- crafted → "crafted"
- broken → "broken"
- picked_up → "picked up"
- dropped → "dropped"
- killed → "killed"
- killed_by → "been killed by"
- custom → use the stat name itself

---

## Badges (36): key → display

Badges are per server and are kept once earned. The display names below are the
agreed names. Map them in the frontend (the backend only stores keys), by family and
tier order. The thresholds in the keys and the "Meaning" column are the defaults; the
live ones are in `/meta.settings.badges`. **Badge keys follow the configured
thresholds**: if a threshold is changed in the backend config, new badges get the new key
(`firsts:20` instead of `firsts:10`) and already earned ones keep the old key. So map
names by family + tier position in the matching `/meta.settings.badges` list (the first
`firsts` entry is Trailblazer, the second Pioneer, the third Founder), not by the number.
A key whose number is no longer in the list is a retired tier: show it with the generic
family name.

| badge_key | Name | Meaning |
|---|---|---|
| `firsts:1` / `firsts:10` / `firsts:50` | Trailblazer / Pioneer / Founder | real firsts held on that server |
| `top1:1` / `top1:10` / `top1:50` | Champion / Dominator / Legend | #1 in that many stats at once |
| `milestone:1000000` / `milestone:10000000` | Millionaire / Ten-Millionaire | a single count stat passed 1M / 10M |
| `cat:<category>:1` / `:2` / `:3` | "<Noun> I / II / III" | category sum ≥ 100K / 1M / 10M |
| `veteran` | Veteran | 1000 h playtime |
| `regular` | Regular | active on ≥ 20 of the last 30 days |
| `night_owl` | Night Owl | mostly plays 00:00–06:00 server time |
| `overtaker` | Overtaker | passed the player above in a rivalry |

Category nouns:

| category | noun |
|---|---|
| `minecraft:mined` | Miner |
| `minecraft:used` | Builder |
| `minecraft:crafted` | Crafter |
| `minecraft:broken` | Toolbreaker |
| `minecraft:picked_up` | Collector |
| `minecraft:dropped` | Dropper |
| `minecraft:killed` | Hunter |
| `minecraft:killed_by` | Victim |

## Not available (out of v1 scope)
- A public/export API.
- Scoreboard correlation views.
- Storage tracking.
- Activity (`/activity`, the heatmap and pulse clock) before the live snapshot was
  deployed, even after a backup import. Backups are months apart and cannot say who was
  online when.
- Any history older than the oldest imported backup.
