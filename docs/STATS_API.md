# Chronos Stats API: contract for the website

Lives in Chronos-Bot at `docs/STATS_API.md`; copy it into the website repo next to `STATS_PLAN.md`. It describes every
endpoint the Stats pages can use, exactly as the backend (Chronos-Bot, `master`)
implements it; it is live at `https://www.chronosmc.com/api/stats/`. `STATS_PLAN.md` is the scope and feature list; this file is
the data contract.

**What changed (round 2, 2026-11-26)**, per item of the website's request:
1. **Fake players are bots on every server** (see Scopes and bots). New
   `/meta.servers[].has_bots` (where to show the toggle: today smp and every CMP except
   building), `/search?include_bots=`, and `is_fake` / `is_bot` on `/player` and
   `/compare.players[]`.
   1b. **Slur filter**: players whose name or a former name contains a slur are gone from the
   whole API, even with `include_bots=true`; `/player` and `/compare` answer 404. They stay
   inside whole-server `/totals` lines (no name is shown there).
2. **Player heads on our server**: `/cache/heads/<uuid>.png` (see Images below). No file
   (404) for fake players, slur-blocked players, players without a skin and players not
   fetched yet: keep the site's fallback.
3. **Item icons from the game files**: `/cache/items/<id>.png` now exists for every item id
   of the server's version, rendered like the inventory (see Images below).
4. **New `GET /rankings?by=top1|firsts|badges`** for "Top players".
5. **`/totals`**: new `starts` (one per line), and `effective_from` is now the **earliest**
   line start (it was the latest), so each line begins at its own data.
6. **`/leaderboard?q=`**: name filter; rows keep their real rank.
7. **Former names**: `/player` gains `aka`; `/search`, `/leaderboard?q=` and `/player/<old
   name>` match former names. Renamed players' old scoreboard scores now count for the
   current name (NotQuiteRat_ -> NotQuiteRat, Q1306 -> DiggusMaximus123, EulerGauss ->
   UsernameAccount, Obi0081_ and Obi81 -> ObiWan81_, lukepourquoi -> justlukie; Alugia7 ->
   Algi_ is handled in-game with stattransfer), and replaced accounts are merged into the
   new one (NotAless50_ -> IBeatLIMBO, wRongo_ -> _wRongo, qstrql -> qstrqlw, plus the
   earlier LordHamster42 -> hampter42). Merged accounts are **added together** (not "the new account's value once
   it exists"): the in-game `stattransfer` moved only scoreboard scores and reset the old
   name, and never touched stat files, so the old account's vanilla stats are its own.
8. **`/search`**: a space matches `_` ("redstone block" finds `minecraft:redstone_block`).

Not part of the Stats API, but for the site: on the **status page**, list `building` and
`building2` after `mirror` and before `snapshot` (owner's request). `data/server_status.json`
stays alphabetical, so the site orders it.

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
- `/leaderboard` and `/rankings` share the row shape `{rank, uuid, name, value}`; ties share
  a rank (1, 1, 3).
- `name` can be `null`: about 1.6k old players have no known name yet (lookups fill
  them in slowly). Show the first 8 characters of the uuid plus the head instead.

### Scopes and bots (apply on every page)
- `scope` is one of `all`, `smp`, `cmps`, or a single server key such as `cmp2` or
  `building`. `/meta.scopes` lists the valid ones (group scopes like `cmps` are set in
  the backend config, so take the list from there rather than hardcoding it). The UI needs **All / CMPs / SMP**
  as the main switch, plus a picker for single servers.
- `include_bots` (default `false`) hides **bots**, which are two things:
  - **Fake players, on every server**: Carpet fake players with made-up names have
    offline-mode uuids (version 3: the 15th character of the uuid is `3`); real Mojang
    accounts are version 4. A fake player spawned under a real account's name gets that
    account's real uuid and can't be told apart.
  - **SMP's team rule**: on SMP, players in no team are bots (named automation accounts).

  Bots are hidden by default on leaderboards, `/series`, `/compare`, `/totals`, `/search`,
  the feeds (`/milestones`, `/firsts`, `/rivalries`), `/spotlight` and `/activity`, and
  included with `include_bots=true`. Offer the toggle where `/meta.servers[].has_bots` is
  true for a server in view. A fake player never holds a first (like SMP bots; there is one
  first per stat).
- **Slur filter (hard block):** a player whose name or a former name contains a term from the
  backend's blocklist is gone from every response **even with `include_bots=true`**: search,
  leaderboards, `/rankings`, `/series`, `/compare` and `/player` (both 404), feeds,
  spotlight and activity. Their numbers stay only inside whole-server `/totals` lines.
- Multi-server scopes **sum** a player's values across servers.

### Time ranges
`range=1d|1w|1m|6m|1y|all`, or `from=<unix>&to=<unix>` (both optional). The presets
are backend config: build the range picker from `/meta.settings.range_presets`
(name → seconds) plus `all`, which is always valid.
- No `range`/`from` → **current totals** (`mode: "total"`). With `to` only → totals as of `to`.
- With `range` or `from` → **the gain over that window** (`mode: "delta"`). That includes
  `range=all`: it is the gain since tracking began, not the current total (`mode: "delta"`).
  `/series` and `/totals` are the exception: they always return cumulative values (step
  lines) over the window, never gains.
- History starts at the server's **tracking start**, `/meta.tracking_started_at[server]`.
  Live tracking began on 2026-11-24, but old world backups are imported
  (`tools/import_backups.py`), which moves the tracking start back to the oldest backup, so
  `tracking_started_at` and `effective_from` can be **years earlier**. Today SMP starts
  2021-10-28 and the CMPs 2026-11-24; more backups may be imported later, so never
  hardcode either date.
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
  `from=2021-05-01` gives `effective_from` = the 2021-09 backup. Windows starting after
  the first live snapshot are never moved.
- **A multi-server scope starts where all of its servers have data**: `effective_from` is
  the latest of its servers' starts, each server's start being the window start clamped to
  that server's own tracking start and then snapped as above. Otherwise a server whose
  tracking began later would count its whole total as gain. So `scope=all&range=all`
  starts when the last server began tracking (2026-11-24 today), even though SMP alone
  goes back to 2021: use `scope=smp` for SMP's long history. Show the same "since <date>" note whenever
  `effective_from` > the requested start.
- A window that ends before tracking began returns empty rows.
- A custom window lying **entirely inside** that sparse span (`from` and `to` both
  between two backups, or between the last backup and the first live snapshot) contains
  no observation. Then `/series` and `/totals` return one point per player / scope,
  `[to, value known at to]` (from the backup before `to`), with `effective_from` = `to`;
  a `/leaderboard` delta (and a `/compare` delta) is empty / 0, since nothing was observed
  in the window. Total mode (`to` only) is unaffected and returns the value at `to`.
- Between backups (and between the last backup and the first live snapshot) history has
  only a few points, so graphs over imported periods are coarse step lines. The boundary
  is `/meta.live_started_at[server]`: before it, sparse imported points; from it on, live
  tracking. Use it to shade or split the imported span on a time axis. For a multi-server
  scope, take the latest of its servers' values.

### Units (raw values → display)
- `minecraft:custom` keys ending in `_one_cm` are **centimetres**: /100 = m, /100000 = km.
- `minecraft:play_time`, `minecraft:total_world_time`, `minecraft:time_since_death`,
  `minecraft:time_since_rest` and `minecraft:sneak_time` are **ticks**: /20 = s,
  /72000 = h.
- `minecraft:damage_*` keys are in **tenths of a heart-point**: /10.
- Everything else is a plain count. Format big numbers compactly (1.2M) with the
  exact value in a tooltip.
- Stat keys look like `category=minecraft:mined`, `key=minecraft:stone`. Display them
  without the namespace and with spaces ("Mined · Stone"). Item icons: see Images.

### Images (static files, not under `/api/stats/`)
- **Player heads: `/cache/heads/<uuid>.png`**, the uuid exactly as the API returns it (lowercase,
  with dashes). 64x64 PNG, pixel art (draw with `image-rendering: pixelated`): the skin's 8x8
  face with the hat layer on top. Written by the snapshot (every 15 min, a bounded number
  of Mojang lookups per run): new players within a few runs, every player re-checked about
  once a day (`stats_tracking.heads`). **404 = use the fallback**: fake players
  (`is_fake`), slur-blocked players, accounts without a custom skin (the default
  Steve/Alex-style skins), and players not fetched yet. On a 404 keep the site's current
  fallback (third-party head, then the letter square).
- **Item icons: `/cache/items/<id>.png`**, the bare id (`dropper.png`, `creeper_spawn_egg.png`,
  `player_head.png`): 64x64 PNG, pixel art, for **every item id** of the server's Minecraft
  version, rendered from the client jar like the inventory (blocks isometric, flat items
  flat; chests, shulker boxes, heads, beds, banners, shields and the conduit as their 3D
  models). Not exact (drawn as their particle texture): copper golem statues, the decorated
  pot, the dragon head. A stat key `minecraft:stone` -> `stone.png`. Ids that no longer exist
  in the current version (renamed in an update, e.g. `grass` -> `short_grass`) have no
  icon, so the site needs its own override for those; the operator's render report lists the
  Storage names concerned.

---

## Endpoints

### `GET /meta`
Load this once per page.
```json
{"data_version": "4127", "last_run_at": 1795600000,
 "servers": [{"key":"smp","label":"SMP","group":"smp","filters_bots":true,"has_bots":true,
              "track_stats":true,"track_scoreboards":true}, …],
 "scopes": ["all","smp","cmps","cmp","cmp2","cmp3","cmp4","building"],
 "scope_groups": {"cmps": ["cmp","cmp2","cmp3","cmp4","building"]},
 "categories": ["minecraft:broken","minecraft:crafted","minecraft:custom", …],
 "tracking_started_at": {"smp": 1635379200, "cmp": 1795528255, …},
 "live_started_at": {"smp": 1795528255, "cmp": 1795528255, …},
 "settings": {…}}
```
- Use `last_run_at` for an "updated N min ago" label.
- **Order:** `servers` and `scopes` come in the backend config's server order (today smp,
  cmp, cmp2, cmp3, cmp4, building). **Keep that order; don't sort.** The same order is
  used for every other list of server keys: `scope_groups` values, `/player.servers` and
  `/search` objective `servers`. Changing the order is a backend config edit only.
- `tracking_started_at`: per server key, unix seconds of the oldest history point
  (the oldest imported backup if there is one, else the first live snapshot). Servers
  with no history yet are missing from the map.
- `live_started_at`: per server key, unix seconds of that server's **first live**
  (non-imported) snapshot: where sparse imported history ends and dense 15-minute
  tracking begins (see Time ranges). Equal to `tracking_started_at` for a server without
  imported backups. Servers with no live snapshot yet are missing from the map.
- `has_bots: true`: the server has bots (fake players, or the SMP team rule), so
  `include_bots` changes what it shows. Show the "Include bots" toggle when a server in view
  has it. `filters_bots: true` only marks the SMP team rule (smp); don't use it for the toggle.
- `track_stats: false` / `track_scoreboards: false`: that server's stats / scoreboards are
  not tracked, and the API leaves them out of every response (leaderboards, series,
  totals, player, search, keys, feeds). The server itself stays in `servers` and `scopes`.
  So a scope can have no scoreboard data at all: show an empty state, not an error.
- `scopes` is in menu order: `all`, single servers outside any group (`smp`), group
  scopes (`cmps`), then the servers inside groups.
- `scope_groups`: each group scope → its server keys (in the order above). Use it to know
  which servers a group scope covers (e.g. "does this scope include SMP" for the bots
  toggle); don't derive it from names. Only group scopes with at least one server appear,
  the same ones `scopes` lists. A single-server scope covers just that server; `all`
  covers every entry of `servers`.

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
  caps `/milestones`, `/firsts` and `/rivalries`, `search` caps `/search`, `series_players` is the
  most uuids `/series` takes, `series_points` the most points per line,
  `player_milestones` how many milestones `/player` returns. A larger `limit` is clamped,
  not rejected.
- `compare`: how many players (min/max) and keys `/compare` accepts.
- `badges`: the thresholds behind each badge key, for "Pioneer: 10 real firsts" style
  descriptions and progress bars. Badge **display names stay in the frontend**, keyed by
  family and tier order (the first `firsts` tier is Trailblazer, the second Pioneer, the
  third Founder; `cat:<category>:1/2/3` are I/II/III, and so on).

### `GET /search?q=sto&limit=10&include_bots=false`
Autocomplete. It answers from memory, so call it on every keystroke (debounce about 120 ms).
```json
{"players": [{"uuid":"…","name":"Stonks"}],
 "stats": [{"category":"minecraft:mined","key":"minecraft:stone"}, …],
 "objectives": [{"objective":"digs","servers":["smp","cmp"]}]}
```
- Matching is case-insensitive and **a space matches `_`**: "redstone block", "Redstone
  Block" and "redstone_block" all find `minecraft:redstone_block`; the same holds for
  objectives and player names. Prefix matches come first, then shorter ones.
- Players are also found by a **former name** (`aka`); the result shows the current name.
- `include_bots` (default false): bots (fake players, SMP team-less players) are only
  offered with `include_bots=true`. Slur-filtered players are never offered.
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
- `q` (optional): name filter, a case-insensitive substring of the player's name or a
  former name (`aka`); a space matches `_`. Rows keep their **real rank** on the unfiltered
  board, and `total_count` is the number of matches (page with `limit`/`offset` as usual).
  No match: `total_count: 0`, `rows: []`. A blank `q` is no filter.

```json
{"effective_from": 1795527876, "to": 1795600000, "mode": "total",
 "total_count": 1087,
 "rows": [{"rank":1,"uuid":"…","name":"PapiChase","value":17327}, …]}
```
- Ties share a rank (1, 1, 3).
- In total mode, players with a value of 0 are left out too (an unused stat or a reset
  score is no standing); `total_count` counts only the listed players.
- In delta mode, players with a gain of 0 are left out.
- Stat gains are never negative (vanilla stats only count up; a drop means the stat was
  reset, which counts as no gain). Scoreboard gains **can** be negative: an objective may
  go down. The same holds for `/compare` values in delta mode.

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
{"effective_from": 1635426651, "to": …,
 "starts": {"smp": 1635426651, "cmp": 1795528261},
 "series": {"smp": [[ts,total],…], "cmp": [[ts,total],…]}}
```
- Same step format as `/series`. **Each line starts at its own data**: `starts[scope]` is
  that line's own start (the window start clamped to that scope's tracking start and gap
  snapped, exactly what a single-scope request gets), and the line's first point is there.
  With one line per server, SMP's line goes back through its imported history while the
  CMPs' lines begin at their own later start.
- `effective_from` = the **earliest** of the `starts` (use it as the axis start); `to` is
  shared. A scope with no data in the window has `starts[scope] = null` and `[]`.
- With a range the lines are **running totals** over the window (the whole-server total
  at each point), not gains; a line's gain is its last value minus its first. A
  multi-server scope (`all`, `cmps`) still starts where all of its servers have data.
- Slur-filtered players' numbers are included here (no name is shown).

### `GET /player/{uuid-or-name}`
The player profile ("dossier"). Params: `scope`, `include_bots`.
```json
{"uuid":"…","name":"Chezloc","aka":["Chez_old"],"name_resolved":1,"last_activity":1795599000,
 "is_bot_on_smp": false, "is_fake": false, "is_bot": false, "servers": ["smp","cmp"],
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
- A name lookup is case-insensitive and also accepts a **former name**.
- `aka`: the player's former names (renames and merged accounts' names), without the current
  name, sorted; `[]` for most players. Show it as "also known as".
- 404 for a slur-filtered player (as if it doesn't exist).
- Merged accounts: separate accounts the server counts as one player (e.g. an alt) resolve
  to the main one. `/player/<alt name or uuid>` returns the main player, so the response
  `uuid`/`name` can differ from what was asked; use them for links. The alt never appears
  in search or leaderboards. `/compare` resolves the same way.
- `milestones` holds the latest 50 entries (`settings.limits.player_milestones`).
- "Best ranks" = sort `stats` by `rank / of`.
- `stats` and `scoreboards` leave out values of 0 (summed over the scope): an unused
  stat (or a junk key such as `crafted/minecraft:air`) or a reset score has no rank.
  `of` counts only players with a non-zero value. The same 0 rule applies to `/keys` and
  `/search` (a stat nobody has a non-zero value for is not offered). `top1:*` badges
  never counted 0 values. Air (`minecraft:air`, `cave_air`, `void_air`) is not tracked
  in any category.
- Bot flags: `is_fake` (a Carpet fake player, any server), `is_bot_on_smp` (SMP team rule),
  `is_bot` = either. If `is_bot` is true the player's own numbers still show (people reach
  the page from a board with bots on); label it as a bot.

### `GET /compare`
N-way compare. Params:
- `uuids=` 2–6 names or uuids (`settings.compare.min_players` / `max_players`)
- `keys=minecraft:mined|minecraft:stone,scoreboard|digs`: up to 20 keys
  (`settings.compare.max_keys`); each key is
  `category|key` for stats or `scoreboard|<objective>`
- `scope`, `range` or `from`/`to`, `include_bots`

```json
{"players": [{"uuid":"…","name":"…","is_bot_on_smp":false,"is_fake":false,"is_bot":false}, …],
 "effective_from": …, "to": …,
 "values": {"minecraft:mined|minecraft:stone": {"<uuid>": 123, …}, …},
 "series": {"minecraft:mined|minecraft:stone": {"<uuid>": [[ts,v],…]}, …}}
```
- A bot shows 0 unless `include_bots=true`. If a compared player has `is_bot: true` (in
  `players`, same meaning as on `/player`), turn the bot toggle on for the comparison.
- A slur-filtered player in `uuids=` gives a 404.
- `players` holds each **distinct** player once, in request order. Names/uuids that
  resolve to the same player (a merged alt and its main account, or a name and its uuid)
  collapse into one entry, so `players` can be shorter than `uuids=`. If fewer than
  `settings.compare.min_players` distinct players remain, it's a 400.

### `GET /rankings?by=top1|firsts|badges`
"Top players": players ranked by a count. Params: `by` (default `top1`), `scope` (default
`all`), `include_bots` (default false), `limit` (default 50, max
`settings.limits.leaderboard`) and `offset`.
```json
{"by": "top1", "total_count": 57,
 "rows": [{"rank":1,"uuid":"…","name":"PapiChase","value":412}, …]}
```
- `by=top1`: how many stats **and** scoreboard objectives (vanilla-copy objectives left out)
  the player is #1 in right now (ties included), on the scope-summed current value > 0. Same
  rule as the `top1:*` (Champion) badges, which count stats only, per server.
- `by=firsts`: how many `/firsts` rows the player holds in the scope, **without** the
  `earliest_known: 1` ones ("already had it when tracking began"); `approximate: 1` ones
  count (as for the Firsts badges).
- `by=badges`: how many distinct badge keys the player has on servers in the scope
  (server-less badges count everywhere).
- Players with 0 are left out; ties share a rank. **Fake players never appear** (not even
  with `include_bots=true`); SMP team-less bots follow `include_bots`; slur-filtered players
  never appear. A bad `by` or a negative `offset` is a 400.

### `GET /milestones?scope=all&limit=50&before=<cursor>`
The milestones feed. `limit` max `settings.limits.feed` (200).
```json
{"rows": [{"id":…,"at":…,"server_key":"smp","uuid":"…","name":"…",
           "category":"minecraft:mined","key":"minecraft:stone",
           "threshold":1000000,"earliest_known":0,"approximate":0}, …],
 "next": "0:1795599000:812"}
```
- Pass `next` back as `before` for the next page; `null` means the end.
- Rows can be for a **scoreboard objective**: `category: "scoreboard"`, `key` = the
  objective name (e.g. `{"category":"scoreboard","key":"digs"}`). Show them as
  "Scoreboard · digs". The same holds for `/firsts` rows and the `/player` milestones and
  firsts (there the field is `stat_key`).
- Real crossings (`earliest_known: 0`) come first, newest first. Then the "already had
  before tracking" ones (`earliest_known: 1`). Show those under a separate, quieter
  heading such as "Already reached before tracking began", without a date.

### `GET /firsts?scope=all&order=oldest|newest&limit=…&before=<cursor>`
`{"rows": [{"server_key","category","key","uuid","name","at","earliest_known","approximate"}, …]}`
- `category: "scoreboard"` rows are firsts on a scoreboard objective (`key` = objective
  name), as in `/milestones`.
- `order=oldest` (the default): **every** row, oldest first (`at`, then insert order). It
  is large on `all` (~17k rows, ~3.4 MB), so use it only for a full list. Optional `limit`
  (max `settings.limits.feed`, 200) keeps the oldest N. No `next` field.
- `order=newest`: paged exactly like `/milestones`. Dated firsts (`earliest_known: 0`)
  come first, newest first, then the `earliest_known: 1` ones. `limit` defaults to 50, max
  `settings.limits.feed`. The response adds `"next"`: pass it back as `before` for the
  next page; `null` means the end. For an overview, `order=newest&limit=5`.
- `before` is only valid with `order=newest`; a bad `order` or cursor is a 400.
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
