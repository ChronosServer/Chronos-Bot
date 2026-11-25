# Stats API round 2: fake players, slur filter, renames, rankings (design)

Source: the owner's manual pass over the live site (`TEMP_NEW_PROMPT.md`, items 1, 1b, 4-8).
Items 2 (player heads) and 3 (item icons from the client jar) are separate projects, done
after this one, each with its own short design.

## Why
The site shows things it shouldn't (fake players with slur names in `/search` and CMP
boards), loses history it has (renamed players' scoreboard scores sit unlinked in the
backups), and needs a few new reads for the overview / explorer pages. Every API change is
documented in `docs/STATS_API.md`; the frontend builds only against what is documented.

## Live-data findings (2026-11-25, `TEMP_diag.py`)
- **Fake players = version-3 uuids.** Carpet fake players with made-up names get offline
  uuids (version 3); real Mojang accounts are version 4. Live counts of stat-file holders:
  smp 926 v3 / 1092 v4, cmp 546 / 926, cmp2 20 / 106, cmp3 161 / 254, cmp4 49 / 163,
  building 0 / 58. The v3 set includes slur names (`nigger` on cmp, `floppydonkeynig` on
  cmp4) and fake copies of real names (`obi0081_`, `_wrongo`, `lordhampster42` are v3 next
  to the real v4 accounts). On SMP, 4 v3 uuids pass the team rule today because their name
  equals a team member's name.
  Limit: a fake player spawned with an existing account's name gets that account's real v4
  uuid. Those can't be told apart; on SMP the team rule still catches them.
- **Renames:** scores under an old name are `participant_kind='unresolved'` rows. Example:
  `dig-pickaxe` smp: `NotQuiteRat` (bb8d717d, uuid row) 13,132 at 2021-10-28, and
  `NotQuiteRat_` (unresolved) 1,334,970 / 9,371,069 / 9,713,583 / 9,412,710 at the five
  backups, 0 at the live run (reset by `stattransfer`), NotQuiteRat 10,304,631 live. Summing
  both names per point gives a correct climb (stattransfer adds the old score into the new
  name and resets the old one). `NotQuiteRat_`, `Q1306`, `EulerGauss`, `Obi81`,
  `lukepourquoi` are in no usercache, so nothing links them.
- **New accounts** (different uuids, both v4 in `players`): NotAless50_ c273fdfb ->
  IBeatLIMBO 76e49874, wRongo_ 13b1d964 -> _wRongo 79c7e2d6, qstrql 6920e60b -> qstrqlw
  a53a264c (the owner listed qstrql as a rename, but they are two accounts). LordHamster42 ->
  hampter42 is already merged.

## Goals / non-goals / anti-goals
- Goals: items 1, 1b, 4, 5, 6, 7, 8 of the prompt, each documented in STATS_API.md.
- Non-goals: heads, item icons (next projects); detecting fake players that use a real
  account's name; Mojang name history (doesn't exist).
- Anti-goals: a slur name visible anywhere (even with bots on); double-counted history
  after a merge; a second "is this a real player" rule that disagrees with the scope rules
  (all classification stays in `api/scope.py`).

## Approaches considered
- **Fake-player detection.** (a) uuid version 3, checked in SQL (`substr(uuid, 15, 1) =
  '3'`): no new data, never changes for a uuid, verified on live data. (b) a "no usercache
  entry" rule: fake players are in usercache too (the server caches them), so it doesn't
  separate them. (c) a hand list: unmaintainable (1,700+ fakes). **Chosen: (a).**
- **Slur filter placement.** (a) read-time only, matching names in the API per request:
  the API is read-only and would redo the matching per process. (b) a `blocked_players`
  table rewritten by derive every snapshot run (15 min) from the list files, read by the
  scope fragments: one place, edits apply within one run, no re-import. **Chosen: (b).**
- **Merging accounts (overlap rule).** The prompt suggests "new account's value once it
  exists, the old one before". Owner confirmed: scoreboard scores were moved with
  `stattransfer` (which adds into the new name and resets the old), and stattransfer never
  touches stat files. So: stat files are separate counts (sum is right; "replace" would drop
  the old account's stats), and scoreboards after a transfer have the score under one name
  only (sum is right). **Chosen: keep the existing SUM merge** (`bot/merge_players.py`),
  deviating from the prompt; documented in STATS_API.md.
- **Linking renamed names.** (a) `--reimport-scoreboards` with a name list: only fixes
  import points, not the unresolved live rows. (b) extend the existing one-time merge fold
  (it already sums unresolved rows by name into a uuid's timeline, at every point, import
  and live) to rename entries, and resolve the names in the live snapshot and the import
  from then on. **Chosen: (b).**

## Design

### Config files (repo root, owner-editable, like `player_merges.json`)
- `player_merges.json` gets a `renames` list next to `merges`:
  `{"uuid": "...", "names": ["NotQuiteRat_"], "from": "2021-01-01", "until": "2026-11-24",
  "note": "..."}`; `from`/`until` optional (dates, inclusive, UTC), bounding when the old name
  belonged to this uuid (a name reused by someone else later). Parsed in `api/merges.py`
  (bad file raises, as today). A name in both a rename and a merge entry is an error.
- Seeded entries: renames bb8d717d <- NotQuiteRat_; 5f2a15be (DiggusMaximus123) <- Q1306;
  5e6b97fb (UsernameAccount) <- EulerGauss; 38e6bba5 (ObiWan81_) <- Obi0081_, Obi81;
  69dcb78d (justlukie) <- lukepourquoi. Merges added: c273fdfb -> 76e49874 (names
  NotAless50_), 13b1d964 -> 79c7e2d6 (wRongo_), 6920e60b -> a53a264c (qstrql).
  Alugia7 -> Algi_ is left out (owner handles it with stattransfer).
- `name_blocklist.txt`: one term per line, `#` comments. Seeded by me with racial/ethnic,
  homophobic/transphobic and ableist slurs and common spellings (LDNOOBW narrowed to slurs;
  not plain profanity). `name_allowlist.txt`: exempt names or uuids, one per line.

### 1. Fake players are bots, on every server
- `api/scope.py` owns the rule: `is_fake_uuid(u)` (Python) and the SQL form. In
  `_scope_sql`, `include_bots=false` now also drops rows whose uuid column is a v3 uuid, on
  every server; SMP's team rule stays as is on top. `counted_uuids` and everything in derive
  that uses "counted" follow (firsts, milestones, badges, rivalries, spotlight,
  `total_no_bots`). A fake player never gets new firsts/badges/rivalries, like SMP bots
  today.
- One-time migration step in derive (meta `fake_rule_applied`): rebuild totals history of
  every server (`total_no_bots` changes on the CMPs); delete firsts held by v3 uuids and
  re-seed those keys to the current top counted holder as `earliest_known=1` (the firsts
  table holds one row per key, so the real holder can't be found otherwise).
- `/meta.servers[]` gets `has_bots` (bool): `filters_bots` OR the server has any v3
  participant. The site shows the "Include bots" toggle where `has_bots` is true (today all
  CMPs except building, and smp). `filters_bots` keeps its meaning (SMP team rule).
- `/search` gets `include_bots` (default false): fake players and SMP team-less bots hidden
  unless true (same rule as the leaderboards, over scope `all`).
- `/player` and `/compare.players[]` get `is_fake` (v3 uuid) and `is_bot` (`is_fake` or
  `is_bot_on_smp`); `is_bot_on_smp` stays. `/player/{fake}` still works.

### 1b. Slur filter (hard block)
- Normalise a name: lowercase, drop `_`, leetspeak `0→o 1→i 3→e 4→a 5→s 7→t @→a $→s`,
  collapse repeated letters; same normalisation applied to each term. Substring match.
- derive stage `blocked_names` (every run, before rivalries/badges/spotlight): match every
  `players.name` plus every former name (renames/merges `names`) against the list, minus the
  allowlist; rewrite table `blocked_players(uuid PK, name, term)` (migration v5).
- `_scope_sql` drops uuids in `blocked_players` **always** (bots on or off), so they vanish
  from leaderboards, series, compare, feeds, spotlight, rankings, `/keys` holders, search.
  Derive's counted fragments also drop them (no new firsts/badges, the next player gets the
  #1). `/player/{blocked}` and `/compare` with a blocked uuid answer 404. Whole-server
  `/totals` keep their numbers (derive totals pass `blocked=False`; no name is shown there).
- `tools/check_blocklist.py` prints every match among all player names (and scoreboard
  raw names) with the term that hit, for the owner to review and fill the allowlist.

### 4. `GET /rankings`
- `by=top1|firsts|badges`, `scope` (default all), `include_bots` (default false), `limit`
  (default 50, max `api.leaderboard_max_limit`), `offset`. Response like `/leaderboard`:
  `{"by", "total_count", "rows": [{rank, uuid, name, value}]}`, competition ranking (ties
  share a rank), 0 values left out.
- `top1`: number of stat keys plus scoreboard objectives (mirrors excluded) where the
  player's scope-summed current value is rank 1 (ties included) among counted players,
  value > 0. Same definition as the `top1:*` badges, but across the scope and including
  objectives. Read-time, `@cached`.
- `firsts`: firsts rows in scope with `earliest_known = 0` (approximate ones count, like the
  Firsts badges).
- `badges`: distinct `badge_key`s of the player with `server_key` in scope or NULL.
- Fake players and blocked players never appear; SMP team-less bots follow `include_bots`.

### 5. `/totals` per-line starts
- Each scope's range is parsed on its own (`timeq.parse_range` per scope), so each line
  starts at its own `effective_from`; the response adds `"starts": {scope: ts}`.
  Top-level `effective_from` becomes the earliest of the starts (the axis start) instead of
  the latest (**deviation from the prompt's "keep effective_from as is"**: kept as-is it
  would still cut SMP at the CMPs' start). `to` unchanged. A scope with no data in the window
  has `starts[scope] = null` and an empty series.

### 6. `/leaderboard?q=`
- Case-insensitive substring on the player name, spaces = `_`. Ranks are computed on the
  unfiltered board; filtering happens after ranking; `total_count` is the filtered count.
  Former names (`aka`) match too.

### 7. Renames and new accounts
- `bot/merge_players.py`: rename entries fold too, once per (uuid, name) (meta
  `renamed:<uuid>:<name lower>`): the name's unresolved `scoreboard_snapshots` rows (within
  `from`/`until`) are summed into the uuid's timeline per (server, objective) and its
  `unresolved:<name>` `scoreboard_current` rows added into the uuid's; totals rebuilt for the
  touched servers. Same DB backup before it as merges.
- From then on the live snapshot and the backup import resolve those names to the uuid
  (date-bounded), before usercache / players names.
- `/player` returns `aka`: former names from renames and merges entries (and merged
  accounts' `players.name`), deduplicated, not the current name. `/search` matches them
  (result shows the current name) and `resolve_player` resolves an old name.
- Deploy: the next snapshot folds the new entries; then `tools/import_backups.py --server
  smp --rebuild-only` re-derives approximate firsts/milestones over the merged history.
  Check: NotQuiteRat's dig-pickaxe series climbs 1.35M (2021) ... 10.3M; IBeatLIMBO's
  history includes NotAless50_'s.

### 8. `/search` spaces
- Queries and indexed texts are compared with spaces and `_` treated the same,
  case-insensitive (stats: also without `minecraft:`); objectives too.

### STATS_API.md
Every change above gets its exact request/response shape, and a "What changed (round 2)"
list per item for the owner to copy into the website repo.

## Left to the builder
Exact SQL; index needs (`EXPLAIN QUERY PLAN` tests per CLAUDE.md for any pinned index);
test fixtures. Tests per item via the existing synthetic-world fixtures.
