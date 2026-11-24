# Chronos Stats subsystem — plan

This is the durable record of the Stats feature's scope, as agreed with the
account owner before implementation. It exists so this doesn't live only in a
chat transcript. See `Chronos-Bot/CLAUDE.md` for the data model and
implementation status; this file is scope and feature list.

## Why this exists

A stats page inspired by (but explicitly not copying) a reference site
(Wavetech/WaveStats) that the account owner liked. Must look and feel like the
rest of the Chronos site, not like the reference. Tracks both vanilla
Minecraft stats and custom in-game scoreboards, across SMP and multiple
Creative servers (CMP, CMP2, CMP3, CMP4, Building), with full history so any
player/stat/scoreboard can be viewed over 1 day / 1 week / 1 month / 6 months
/ 1 year / custom range — not just a current snapshot.

## Hard scope rules (explicit decisions, don't relitigate)

- **mirror and tour are never tracked** — near-duplicates of SMP (same
  fake-player names, nearly identical scoreboard objective counts); tracking
  them would double-count activity.
- **SMP excludes bots by default**, filtered via scoreboard team membership
  (in a team = real player, in no team = bot). Toggle exists to include bots
  if ever wanted, but default is off.
- **CMPs (all of them) never filter bots** — always include everyone, no
  toggle. Different ruleset from SMP, by explicit decision, not an oversight.
- **Fake players are excluded everywhere, unconditionally** (`luigiserectcock`,
  `Cane`, etc.) — these are NPCs for automation, never real accounts. See
  bot's CLAUDE.md for the exact resolution rule (usercache-or-stat-file, never
  name-shape heuristics).
- Per-server toggles for `track_stats` / `track_scoreboards` independently —
  e.g. CMP3/CMP4 scoreboards are mostly noise (MotX/health/gt/boatnumber),
  tracked as stats-only.
- Website needs three scopes to browse by: **All**, **CMPs (plural, grouped)**,
  **SMP**, plus a free picker for any individual server.
- Everything dynamic/config-driven — no hardcoded stat or scoreboard lists
  anywhere in frontend or backend. New stats/objectives that appear on the
  server must show up automatically.

## V1 feature list (agreed to build)

Core:
- **Stat Explorer** — search/browse any vanilla stat or custom scoreboard,
  across any server/scope, with autocomplete for both stat names and player
  names, player head icons (via Crafatar), sortable leaderboards.
- **Scoreboards** — separate from vanilla stats view; vanilla-mirrored
  objectives hidden here (already captured via stat snapshots) to avoid
  showing the same number twice under two different names.
- **Player profile / dossier** — a player's full stat/scoreboard history,
  auto-generated badges, rank among peers.
- **Compare** — N-way (3–6 players) side-by-side comparison of any stat(s).
- **Graphs everywhere** — per-player, per-stat, per-scoreboard, with
  selectable timeframe (1d/1w/1m/6m/1y/custom), smooth and fast.
- **Server vs server** aggregate comparison (e.g. SMP totals vs CMP totals).

Creative/original additions (account owner explicitly wanted MORE than the
reference site, not a clone):
- **Milestones** — generic, category-based threshold crossings (e.g. a
  mined-category stat crossing 1K/10K/100K/1M/10M), not per-item hardcoded
  rules. Config-driven thresholds (`milestone_rules` table).
- **Firsts** — first player ever to reach a given stat/scoreboard on a given
  server (`firsts` table, one row per server+category+stat_key).
- **Rivalries / "catching up"** — detect two players close in a stat and
  show the rate at which the gap is closing/widening.
- **Activity heatmap** — visualize play activity over time (likely by hour-
  of-day / day-of-week, derived from snapshot timestamps).
- **Dossier badges** — auto-generated achievement badges per player, derived
  from milestones/firsts/rivalries, not manually curated.
- **Server pulse clock** — a 24-hour radial chart showing when the server is
  typically active.
- **Random stat spotlight** — surface an interesting/unusual stat on load
  (e.g. "X has died to fall damage 847 times").

## Explicitly OUT of v1 scope (deferred, don't build yet)

- Public API / export endpoint.
- Scoreboard rate-of-change / correlation view (comparing two scoreboards'
  trends against each other).
- Storage tracking expansion: stats/graphs for main storage, dig storage, and
  a new concrete/gravity-block storage tracker (concrete storage isn't
  tracked at all yet, separate from this Stats subsystem). Keep in mind for
  later, not part of this build.

## Data model summary (see sql/schema.sql for the real thing)

Change-only history: a row is written only when a value differs from the last
known value, because SMP alone has ~9,346 scoreboard objectives and ~175k
PlayerScores rows — unconditional per-interval snapshotting would be tens of
millions of rows/day. All queries read "latest value at/before timestamp T",
never "sum/aggregate all rows in a range". See bot's CLAUDE.md for the full
rationale and the NBT/fake-player/vanilla-mirror edge cases this had to
account for.

## Website nav (confirmed)

Top-level nav gets one new "Stats ▾" dropdown with sub-pages: Overview /
Explorer / Scoreboards / Player / Compare / Milestones. This replaces six new
top-level nav items. Bolt map and World map stay as their own separate
top-level links — they are NOT folded into the Stats dropdown, since they
aren't part of the same subsystem the way the six stats views are.

## Build order

1. ~~Probe real server data, design schema, write `bot/snapshot.py`~~ — done,
   verified against real production data (see Chronos-Bot/CLAUDE.md status).
2. ~~One more hand-run of `snapshot.py` after a real in-game change~~ — done:
   smp stats wrote 61, smp scoreboards 13, cmp stats 30, everything else 0.
3. ~~Wire `snapshot.py` into a scheduler~~ — systemd oneshot service + 15-min
   timer in `deploy/systemd/` (separate process, not a bot `tasks.loop`, to
   keep the heavy NBT/SQLite work out of the bot).
4. Backend API layer (FastAPI or similar) reading the stats SQLite DB:
   leaderboards, deltas over arbitrary timeframes, graph data, rankings,
   heatmap data, rivalry/catching-up detection, milestone/firsts derivation
   jobs.
5. Website Stats frontend: the six pages listed above, in the established
   Chronos design system (dark panels, teal accent, Syncopate/IBM Plex,
   scroll-reveal — see website's CLAUDE.md), built from the Design/
   Engineering/Impeccable/taste/Emil-Kowalski skill set now installed in
   Claude Code for this.
