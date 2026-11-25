# Chronos Stats API — design

Status: **approved** by the account owner · 2026-11-24. Mojang public API calls (name↔uuid) are explicitly OK'd. · Step 4 of `STATS_PLAN.md`

## Intent

The website's Stats pages (Overview / Explorer / Scoreboards / Player / Compare /
Milestones) need a backend that answers arbitrary questions over the change-only
history in `chronos-stats.db`: any stat or scoreboard, any server scope, any
time range, any set of players. The account owner's bar is **as fast and
seamless as possible**: pages should feel instant, graphs smooth, and
autocomplete should keep up with typing.

`STATS_PLAN.md` is the scope authority (features, hard scope rules, out-of-scope
list). This doc covers only *how* the backend delivers it.

## Goals

- Serve every V1 feature in `STATS_PLAN.md` from one HTTP API.
- Typical response well under 100 ms server-side. Heavy views (player profile,
  server-vs-server) under ~300 ms uncached, and instant once cached.
- Zero hardcoded stat/objective lists. Everything comes from what's in the DB.
- Apply the existing participant rules identically to the snapshot:
  fake players are never shown, SMP hides bots by default (with a toggle), and
  CMPs never filter.

## Non-goals (V1)

- Public/documented API, API keys, export endpoint (deferred in STATS_PLAN).
- Scoreboard rate-of-change/correlation view (deferred).
- Storage tracking (deferred).
- Any write endpoints. The API is strictly read-only.

## Anti-goals (failure even if it "works")

- A query that sums history rows in a range instead of reading "latest row
  at/before T". That's wrong under change-only storage.
- Showing a fake player (`unresolved` scoreboard participant) anywhere.
- Showing the same number twice under two names (vanilla-mirror objectives in
  the Scoreboards view).
- Treating "no data before tracking began" as "value was 0", which would make
  every player's 1-year delta equal their lifetime total.
- Slow first paint because a page fires a dozen sequential requests.

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A** | FastAPI, live read-only SQL per request; heavy derived data (milestones, firsts, badges, activity, totals, rivalries) computed by a **derive step at the end of each snapshot run** | One new always-on service + reverse-proxy rule | **Chosen** |
| B | No API; snapshot writes pre-built JSON to `/var/www/html/data/stats/` like the members export | Can't do custom ranges, N-way compare or autocomplete without a combinatorial explosion of files | Rejected |
| C | FastAPI computing everything (incl. derived data) per request | Heavy pages redo identical work for every visitor | Rejected |

The account owner picked A, on the condition that it's the fastest and most
seamless option. It is: per-request work is all indexed point lookups, and
everything expensive is done once per 15 minutes instead of once per visitor.

## Decisions made with the account owner

- **Approach A** (above).
- **Firsts are seeded from current holders.** History began at run 1, so who
  reached a stat first before tracking is unknowable. On first derive, each
  existing stat/objective gets a `firsts` row for its current top holder,
  flagged `earliest_known = 1` ("earliest known holder"). Firsts recorded after
  tracking began have `earliest_known = 0` and are real firsts. The frontend
  labels the two differently.
- Snapshot runs on a 15-minute systemd timer (`deploy/systemd/`).

## Architecture

```
systemd timer (15 min)
  └─ bot/snapshot.py ── writes stat/scoreboard history (existing)
        └─ bot/derive.py ── NEW: runs right after, same process
              updates: activity, stat_totals, scoreboard_totals,
                       milestones, firsts, badges, rivalries,
                       players.last_activity, meta.data_version

chronos-stats-api.service (uvicorn, 127.0.0.1:8765)
  └─ api/ (FastAPI) ── read-only SQLite connections ──> chronos-stats.db (WAL)

web server (nginx/apache, TBD by recon)
  /api/stats/*  ──reverse proxy──> 127.0.0.1:8765
  /stats/*      ──static──> website pages (step 5)
```

SQLite WAL mode (already on) lets the API read while the snapshot writes. The
API opens connections with `mode=ro` and `PRAGMA query_only=1`. It
physically cannot modify the DB.

### Code layout (in this repo)

```
api/
  main.py          FastAPI app, middleware (gzip, caching headers)
  db.py            read-only connection pool, data_version lookup
  scope.py         scope parsing + participant filtering (the ONE place rules live)
  queries/         one module per feature: leaderboard, series, player, compare,
                   search, activity, milestones, rivalries, spotlight, servers
  cache.py         in-process response cache keyed by data_version
bot/derive.py      derive step, called at end of snapshot.run_once()
sql/schema.sql     + new tables/columns below
sql/migrations.py  idempotent ALTERs, keyed by PRAGMA user_version
deploy/systemd/chronos-stats-api.service
deploy/<webserver>/ reverse-proxy snippet
tests/             pytest, synthetic fixture DB
```

## Shared rules: `api/scope.py`

Every query goes through one function that turns request params into SQL
filters, so the participant rules exist in exactly one place:

- `scope` = `all` | `smp` | `cmps` | a single server key. Groups come from
  `servers.group_key`, never hardcoded. Servers with `provisioned = 0` are
  excluded.
- **Multi-server scopes sum per player across servers** (a player's "All"
  total = their SMP value + each CMP value).
- **Bots:** `include_bots` (default false) only ever affects rows where
  `server_key = 'smp'`. A player flagged as a bot on SMP still counts on CMPs
  in an `all` scope, because the rule is per server.
  **Strict rule (owner, confirmed against live data):** on SMP, anyone not in
  a team is a bot, with no exceptions. A uuid with no `player_flags` row is
  treated as a bot (fail closed).
- **Fake players:** scoreboard rows with `participant_kind = 'unresolved'` are
  excluded from every player-facing result, unconditionally.
- **Vanilla mirrors:** objectives with `scoreboard_meta.is_vanilla_mirror = 1`
  are excluded from scoreboard listings/search (they have no scores stored
  anyway). Removed objectives (`removed = 1`) remain viewable historically but
  are marked as removed.

## Time ranges: the core query

- Range params: `from`, `to` as unix seconds, or `range=1d|1w|1m|6m|1y|all`.
- **Value at T** = latest row with `at <= T` for that (server, key, participant).
  For T = now, read `stat_current` / `scoreboard_current` directly.
  Many-player form, which uses `idx_stat_point`:
  `SELECT uuid, value, MAX(at) FROM stat_snapshots WHERE server_key=? AND category=? AND stat_key=? AND at<=? GROUP BY uuid`
- **Delta over [from, to]** = value_at(to) − value_at(from).
- **Clamp to tracking start.** `tracking_started_at` = first successful
  snapshot run per server. If `from` is earlier, it's clamped, and the
  response includes `effective_from` so the UI can say "since tracking began
  (24 Nov 2026)". A player whose first row appears *after* tracking began
  genuinely started at 0 (new player or newly earned stat).

## Derived data: `bot/derive.py`

Runs after each snapshot run, in the same process, and touches only what
changed in this run (the snapshot already knows which rows it inserted at
timestamp `now`). Failures are recorded in `snapshot_runs` as
`kind='derive'` and never fail the snapshot itself.

| Table (new unless noted) | Contents | Feeds |
|---|---|---|
| `activity(server_key, at, uuid)` | one row per player whose `minecraft:custom/minecraft:play_time` changed this run (only increments while online) | heatmap, pulse clock, `last_activity` |
| `stat_totals(server_key, category, stat_key, at, total, total_no_bots)` | change-only server-wide totals | server-vs-server, overview, stat total graphs |
| `scoreboard_totals(server_key, objective, at, total)` | same for objectives (players only) | scoreboards view graphs |
| `milestones` (exists) + `earliest_known` column | threshold crossings from `milestone_rules` | milestones feed, badges |
| `milestone_rules` (exists) | seeded with category-wide defaults (1K/10K/100K/1M/10M for count categories) plus per-key rules for unit-bearing custom stats (play time in hours, distances in km) | milestones |
| `firsts` (exists) + `earliest_known` column | see decision above | firsts, badges |
| `badges` (exists) | generic, rule-derived (e.g. N firsts, top-1 in N stats, milestone tiers) | profile |
| `rivalries(server_key, category, stat_key, uuid_a, uuid_b, gap, gap_7d_ago, computed_at)` | adjacent pairs in a leaderboard, both active in the last 7 days, gap small relative to value; **replaced** each run | rivalries / "catching up" |
| `players.last_activity` (exists) | max `activity.at` | profile, "active" filters |
| `meta(key, value)` | `data_version` (= latest snapshot run id), `tracking_started_at` per server | caching, range clamping |

Seeding milestones on first derive uses the same honesty rule as firsts:
crossings that already existed before tracking get `earliest_known = 1`
("already had") instead of a fake date.

Fake players and (for `total_no_bots`) SMP bots are excluded using the same
rules as `scope.py`. *My call:* `derive.py` imports the rule functions from
`api/scope.py` rather than reimplementing them.

## Endpoints (all `GET`, JSON, prefix `/api/stats`)

| Endpoint | Returns |
|---|---|
| `/meta` | servers, groups/scopes, categories, `data_version`, last run time, `tracking_started_at` |
| `/search?q=` | autocomplete: players, stat keys, objectives (≤10 each), prefix match ranked above substring match |
| `/keys?scope=&kind=stat\|scoreboard` | every category/key or objective present in scope |
| `/leaderboard?kind=&category=&key=&scope=&range=\|from=&to=&include_bots=&limit=&offset=` | ranked players with value (or delta), rank, name, uuid |
| `/series?kind=&category=&key=&uuids=&scope=&range=` | step series per player, downsampled to ≤ ~500 points |
| `/totals?kind=&category=&key=&scopes=&range=` | server/scope total series (server-vs-server) |
| `/player/{uuid-or-name}?scope=` | per-server current values, rank per stat, badges, milestones, firsts, rivals, last_activity |
| `/compare?uuids=(3–6)&keys=&range=` | side-by-side values + series |
| `/milestones?scope=&limit=&before=` | feed (cursor-paginated) |
| `/firsts?scope=` | firsts table, with `earliest_known` |
| `/rivalries?scope=&uuid=` | closing/widening gaps, rate per day, projected overtake |
| `/activity?scope=&range=` | 15-min UTC buckets of active-player counts (frontend bins to local time for heatmap + pulse clock) |
| `/spotlight?scope=` | one random notable stat ("X has died to fall damage 847 times") |

Pages should need one or two requests each, not a waterfall. `/player` and
`/meta` are deliberately "fat" for that reason.

## Speed

- **`data_version` caching.** Data changes only when a snapshot run lands
  (every 15 min). Every response carries `ETag: "<data_version>-<hash of
  path+query>"` and `Cache-Control: public, max-age=60,
  stale-while-revalidate=900`. Repeat requests are a `304` or a browser-cache
  hit.
- **In-process LRU cache** keyed `(path, query, data_version)`. A new
  snapshot automatically invalidates it, with no manual purging.
- **gzip** middleware; **orjson** responses.
- SQLite tuning on read connections: `mmap_size`, larger `cache_size`,
  `temp_store=memory`.
- Series downsampling server-side, so a 1-year graph never ships thousands of
  points.
- Search index (names + keys) held in memory, rebuilt when `data_version`
  changes, so autocomplete never hits disk.
- 2 uvicorn workers.

## Player names

~1,100 SMP players have no name in any usercache. *My call, flagged because it
contacts an outside service:* a rate-limited name resolver in `derive.py`
looks up unnamed uuids against Mojang's public profile API (a handful per
run, results stored in `players` with `name_resolved = 1`). Only public
Minecraft uuids are sent. Until a name resolves, the UI shows a short uuid
with the Crafatar head (which works by uuid alone). A side benefit: once named, these
players can be evaluated by the SMP team check.

## Deployment

- `deploy/systemd/chronos-stats-api.service`: uvicorn on `127.0.0.1:8765`,
  `Restart=always`, using `/opt/python/bin/python3`.
- **nginx** serves `/var/www/html` (confirmed; sites-enabled: `default`,
  `pufferpanel.conf`). Add a `location /api/stats/ { proxy_pass
  http://127.0.0.1:8765; }` block to the site that serves the website. Same
  origin, so no CORS. Port 8765 confirmed free.
- New deps in `requirements.txt`: `fastapi`, `uvicorn`, `orjson` (none
  installed yet on the server's Python 3.14.10). All have 3.14 support.

## SMP bot rule: confirmed, plus a fix to the team check

Live data: 25 teams, about 196 members total. The top SMP playtimes are mostly
automation accounts (`BedBot` 29k h, `Endswitch`, `digsort`, `ms`, `001`…),
correctly flagged as bots. Of the players that have SMP stats, 119 are real
and 968 are bots. The owner confirmed: **everyone without a team is a bot,
for sure.**

Two gaps in the current `run_team_check` (in `snapshot.py`) need fixing so
the strict rule is applied correctly:

1. **It only flags players that have a name.** Team lists contain names, so a
   current team member whose name isn't in any usercache gets no flag. Under
   the strict rule they'd be wrongly hidden as a bot. Fix: resolve every team
   member name that isn't already known to a uuid. This is a bounded set of
   ~196 names, looked up once and cached in `players`, via Mojang's public
   name→uuid API. Only public Minecraft names are sent.
2. **It flags every player in the DB, not just SMP's.** It should flag every
   uuid that has an SMP stat file: `is_bot = 0` iff its name is in a team,
   otherwise 1.

The general unnamed-player resolver (above) is still useful for display, but
correctness of the bot rule no longer depends on it.

## nginx

The `default` site already proxies `/api/status` to `127.0.0.1:5001`, and the
new block follows the same pattern:

```nginx
location /api/stats/ {
    proxy_pass http://127.0.0.1:8765/api/stats/;
    proxy_set_header Host $host;
}
```

The API sends its own `Cache-Control`/`ETag`. nginx must not add
`no-cache` to this location (unlike the `/data/*.json` rule).

## Testing

- pytest against a synthetic fixture DB built by running the real
  `snapshot.py` functions over fixture stat files/scoreboards at controlled
  timestamps. This tests the real write path, not hand-made rows.
- Must-cover cases: clamp before tracking start; new player mid-range starts
  at 0; bot hidden on smp but counted on cmp in `all`; fake player never
  appears; mirror objectives absent; firsts/milestones `earliest_known`
  seeding happens once and is not re-seeded; derive failure doesn't fail
  snapshot.
- Then a real run on the server, with timings for each endpoint.

## Left to the builder

- Exact SQL per endpoint (within the "value at T" rule).
- Downsampling algorithm (keep step shape: last value per bucket).

Badges, rivalries and spotlight are **not** left to the builder. The owner's
review asked for concrete specs, which are in the addendum below.

---

## Addendum A: Badges, rivalries, spotlight (requested in owner review)

All three are computed in `derive.py` and use only **counted** players (the
`scope.py` rules: SMP bots and fake players never earn or appear). Badges are
**per server** (`badges.server_key` always set) and **kept once earned**, so
losing a #1 spot later doesn't take the badge away.

### A1. Badges: 9 families, 36 distinct badges in v1

| Family | badge_key | Name shown | Rule (per server) |
|---|---|---|---|
| Firsts | `firsts:1` / `:10` / `:50` | Trailblazer / Pioneer / Founder | holds ≥ 1 / 10 / 50 rows in `firsts` with `earliest_known = 0` |
| Champion | `top1:1` / `:10` / `:50` | Champion / Dominator / Legend | currently #1 in ≥ 1 / 10 / 50 stat keys at the same time |
| Big numbers | `milestone:1000000` / `:10000000` | Millionaire / Ten-Millionaire | any single stat crossed 1M / 10M (milestone row, either flag) |
| Category tiers | `cat:<category>:1` / `:2` / `:3` | e.g. "Miner I / II / III" | player's **sum over all keys in the category** ≥ 100K / 1M / 10M, for the 8 count categories (mined, used, crafted, broken, picked_up, dropped, killed, killed_by). 8 × 3 = 24 badges. The display name comes from a category→noun map in the frontend (Miner, Builder, Crafter, Toolbreaker, Collector, Dropper, Hunter, Victim). |
| Veteran | `veteran` | Veteran | `minecraft:play_time` ≥ 1000 h (72,000,000 ticks) |
| Regular | `regular` | Regular | active (has an `activity` row) on ≥ 20 distinct days out of the last 30 |
| Night Owl | `night_owl` | Night Owl | ≥ 40 activity buckets in the last 30 days, and > 50% of them fall between 00:00 and 06:00 server-local time |
| Overtaker | `overtaker` | Overtaker | passed the player directly above them in a tracked rivalry (detected when a stored pair's order flips between runs) |
| Firsts (seeded) | none | n/a | `earliest_known = 1` firsts **don't** count toward Firsts badges. A badge implies a real, observed achievement. |

Earliest-known milestones **do** count for "Big numbers" and Category tiers,
because those are about the value reached, not the date. Badges based on
activity (Regular, Night Owl) can't be earned until enough activity history
exists. That's expected, and they appear as the history accumulates.

### A2. Rivalries

A rivalry is a pair of **adjacent** counted players on one server's ranking
for one stat (A directly above B) where all of these hold:

| Rule | Initial value | Why |
|---|---|---|
| Leader's value is meaningful | `value_a ≥ 1000` | drops "3 vs 2 ghast kills" noise |
| Gap is small | `gap ≤ 2%` of `value_a` (tuned, see below) | "close race" |
| Both are active | both have an `activity` row in the last 7 days | owner's rule |
| **The race is moving** | `gap ≠ gap_7d_ago` and B gained ≥ 1 on this stat in the last 7 days | removes two players who happen to sit 1 apart and aren't playing that stat |
| Not swamped | keep at most **5 rivalries per player** (lowest `overtake_days` first, then smallest relative gap) and **3 per stat key** | stops one hyperactive player or one common stat (dirt mined) flooding the list |

`gap_7d_ago` uses value-at(run_at − 7 d), clamped to tracking start.
`closing_per_day` and `overtake_days` are computed as in the plan. Overtaker
detection: before replacing the rivalries table each run, any stored pair
whose B is now ≥ A awards B the `overtaker` badge.

**Tuning (done 2026-11-24 against the live DB):** with the leader ≥ 1000,
adjacent counted pairs number 3,981 at ≤0.5% gap, 6,410 at ≤1%, 9,568 at ≤2%,
15,066 at ≤5% and 20,567 at ≤10% (~1,500 players involved, avg 12 pairs/player at 2%).
**Kept at 2%.** Real close races sit right at that edge (e.g. crafting tables
17,327 vs 16,988 = 2.0%), and volume is controlled by the "active" and "moving"
filters plus the 5/player and 3/key caps, not by shrinking the gap. Re-check
after 7 days of activity history exist (the activity filter couldn't be
measured yet: see next note).

**Tracking start must ignore empty runs.** The live DB contains early
`ok=1` stats runs with `entries_read=0` (from before the path fixes). So
`tracking_started_at`, and "is this the first run" checks, use
`MIN(at) WHERE kind='stats' AND ok=1 AND entries_read > 0`. Otherwise range
clamping and first-run activity skipping are anchored to a run that saw no data.

### A3. Spotlight: 5 triggers, rotated

Derive rebuilds a `spotlight_candidates(trigger, server_key, uuid, category,
stat_key, value, detail_json, computed_at)` table each run. The API picks a
**random trigger type first** (from the types that have candidates), then a
random candidate within it, so the same obvious stat can't dominate.

| Trigger | Candidate when | Example line |
|---|---|---|
| `outlier` | holder's value ≥ **10×** the median of non-zero holders, with ≥ 10 holders | "Placer has mined 40× more obsidian than the average player" |
| `rare` | stat held by ≤ 3 counted players ever (value > 0); the top holder | "Only 3 players have ever killed an Elder Guardian. Raid has 12." |
| `recent_milestone` | milestone with `earliest_known = 0` in the last 7 days | "PapiChase crossed 1,000,000 stone mined yesterday" |
| `recent_first` | first with `earliest_known = 0` in the last 30 days | "justlukie was the first to craft a crafter on SMP" |
| `mover` | top 20 7-day deltas where the delta ≥ 50% of the player's value 7 days ago (and ≥ 1000) | "voiding_ doubled their netherrack mined this week" |

Anti-repetition: `/spotlight?seen=k1,k2,…` excludes `(category, key)` pairs the
frontend has shown recently (the frontend keeps the last 10 in
`sessionStorage`). Early on, `recent_*` and `mover` are empty, so it
falls back to `outlier` and `rare`, which work from day one.
