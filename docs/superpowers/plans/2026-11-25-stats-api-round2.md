# Stats API round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide fake players (v3 uuids) like bots everywhere, hard-block slur names, link renamed/replaced accounts' history, and add `/rankings`, `/totals` per-line starts, `/leaderboard?q=` and space-tolerant `/search`.

**Architecture:** All participant rules stay in `api/scope.py` (`_scope_sql` gains the fake rule and the block rule); derive keeps `blocked_players` current each run; renames reuse the one-time fold in `bot/merge_players.py` and the live/import name resolution. New read code lives in `api/routes/`.

**Tech Stack:** Python 3.13/3.14, SQLite, FastAPI, pytest (synthetic worlds via `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-25-stats-api-round2-design.md`

## Global Constraints
- Fake player = uuid string of length 36 whose 15th char (index 14) is `'3'`. SQL: `(length(X)=36 AND substr(X,15,1)='3')`.
- `include_bots=false` hides fake players on every server; SMP team rule (`filters_bots=1`) unchanged on top.
- Blocked players (in `blocked_players`) are hidden in every API read, bots on or off; derive totals (`total`, `total_no_bots`) still include them.
- Name normalisation: lowercase, remove `_`, map `0→o 1→i 3→e 4→a 5→s 7→t @→a $→s`, collapse runs of the same letter to one; substring match of normalised term in normalised name.
- Config files at repo root: `player_merges.json` (adds `renames`), `name_blocklist.txt`, `name_allowlist.txt`. Tests never read the repo's copies (autouse fixtures).
- No new tuning literals in bot/derive*, bot/snapshot.py or api/ (CLAUDE.md); new limits reuse existing settings (`api.leaderboard_max_limit`).
- Merging stays SUM (stat files never transferred; stattransfer resets the old scoreboard name).
- Every API change documented in `docs/STATS_API.md` with exact shapes.
- Run tests: `python -m pytest tests -q` (all must pass at every commit).

## Review Focus
- A renamed name whose `from`/`until` excludes some import points: rows outside stay unresolved (test in Task 5).
- A blocked player who is also a merge target / has `aka` names: block matches on any former name (test in Task 3).
- `/leaderboard?q=` with no matches → `total_count: 0, rows: []`, never an error (test in Task 8).
- `/totals?scopes=smp,cmp` where one scope has no data in the window → its `starts` is `null`, series `[]` (test in Task 7).
- Existing tests whose fixture uuids happen to be version 3 (`3333…`-style values with `3` at index 14) must be adjusted, not the rule (Task 1).

---

### Task 1: Fake-player rule in `api/scope.py` and derive's counted sets

**Files:**
- Modify: `api/scope.py` (`_scope_sql`, `is_counted`, add `is_fake_uuid`, `FAKE_SQL`)
- Modify: `bot/derive.py` (`_backfill_server.is_counted`)
- Test: `tests/test_fake_players.py` (new); fix fixture uuids in any test that breaks only because a uuid has `3` at index 14

**Interfaces:**
- Produces: `api.scope.is_fake_uuid(u: str) -> bool`; `api.scope.fake_sql(col: str) -> str` (returns `(length(col)=36 AND substr(col,15,1)='3')`).
- `_scope_sql(scope, include_bots, a, uuid_col)`: when `include_bots` is False adds `AND NOT <fake_sql(a.uuid_col)>`.

- [ ] **Step 1: Write failing tests** in `tests/test_fake_players.py` with `FAKE = "aaaaaaaa-aaaa-3aaa-8aaa-aaaaaaaaaaaa"`, `REAL = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"`:
  - `test_is_fake_uuid`: `is_fake_uuid(FAKE)`, not `is_fake_uuid(REAL)`, not `is_fake_uuid("u1")`.
  - `test_cmp_leaderboard_hides_fake_unless_include_bots`: both have stats on `cmp`; `/leaderboard?scope=cmp&category=minecraft:mined&key=minecraft:stone` rows uuids == `[REAL]`; with `include_bots=true` both.
  - `test_cmp_total_no_bots_excludes_fake`: after a snapshot, `stat_totals_current` on cmp: `total` = both, `total_no_bots` = REAL only.
- [ ] **Step 2:** `python -m pytest tests/test_fake_players.py -q` → FAIL.
- [ ] **Step 3: Implement** `is_fake_uuid`, `fake_sql`, the `_scope_sql` clause, `is_counted` returns False for a fake when not `include_bots`; `_backfill_server.is_counted(u)` = `not is_fake_uuid(u) and (counted is None or u in counted)`.
- [ ] **Step 4:** Full suite. Fix tests that fail only because their fixture uuid is v3-shaped (change the digit at index 14 to `4`); any other failure is a real regression to fix.
- [ ] **Step 5: Commit** `feat(stats): fake players (v3 uuids) are bots on every server`.

### Task 2: One-time fake migration, `has_bots`, `is_fake`/`is_bot`, `/search include_bots`

**Files:**
- Modify: `bot/derive.py` (`run_derive`: new stage `apply_fake_rule(conn, run_at)` after `derive_totals`, before `derive_firsts`)
- Modify: `bot/derive_achievements.py` (expose `reseed_firsts(conn, run_at)` = `_insert_firsts(conn, _sources(key, run_at)["current"], run_at, 1)` for each server)
- Modify: `api/routes/meta.py`, `api/routes/player.py`, `api/routes/search.py`
- Test: `tests/test_fake_players.py`

**Interfaces:**
- Consumes: Task 1 `fake_sql`, `is_fake_uuid`.
- Produces: meta key `fake_rule_applied` = `"1"`; `/meta.servers[].has_bots: bool`; `/player.is_fake`, `/player.is_bot`, `/compare.players[].is_fake/is_bot`; `/search?include_bots=`.

- [ ] **Step 1: Failing tests:**
  - `test_fake_rule_migration_reseeds_firsts`: build a DB where a fake holds a `firsts` row on cmp (insert directly, meta `fake_rule_applied` absent), run a snapshot; the row is gone and the key's first is REAL with `earliest_known=1`; meta `fake_rule_applied == "1"`; a second run changes nothing.
  - `test_meta_has_bots`: cmp with a fake → `has_bots` true; cmp2 without → false; smp → true (filters_bots).
  - `test_player_fake_flags`: `/player/{FAKE}` 200 with `is_fake: true, is_bot: true`; REAL: both false.
  - `test_search_hides_fake_unless_include_bots`: fake named `Fakey` on cmp not in `/search?q=fak`; present with `include_bots=true`. SMP team-less bot likewise.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** `apply_fake_rule`: if meta missing: `rebuild_totals(conn, run_at, all servers)`, `DELETE FROM firsts WHERE <fake_sql('uuid')>`, `reseed_firsts`, set meta; same transaction, commit. `has_bots` = `filters_bots OR EXISTS fake uuid in stat_current or scoreboard_current participant_key on that server`. `/search` index keeps per player a `bot` bool (fake, or not counted by the SMP rule on every bot-filtering server it appears on, i.e. not in the `include_bots=False` counted set) and filters at query time; index built from `stat_filter_sql(sc, True, ...)`.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): fake-rule migration, has_bots, is_fake/is_bot, search include_bots`.

### Task 3: Slur filter

**Files:**
- Create: `api/names.py` (normalise + lists, pure stdlib), `bot/derive_blocked.py` (stage), `tools/check_blocklist.py`, `name_blocklist.txt`, `name_allowlist.txt`
- Modify: `sql/migrations.py` (`_v5`: `CREATE TABLE IF NOT EXISTS blocked_players (uuid TEXT PRIMARY KEY, name TEXT, term TEXT)`), `api/scope.py` (`_scope_sql(..., blocked=True)`; `stat_filter_sql`/`scoreboard_filter_sql` pass `blocked` through), `bot/derive.py` (totals pass `blocked=False`; stage runs first in `run_derive`), `api/routes/player.py` (404 for blocked in `/player` and `/compare`), `tests/conftest.py` (autouse: `api.names` paths → empty tmp files)
- Test: `tests/test_blocklist.py`

**Interfaces:**
- Produces: `api.names.normalize(s: str) -> str`; `api.names.load_terms(path=None) -> list[str]` (normalised, deduped, `#` comments and blanks skipped); `api.names.load_allow(path=None) -> set[str]` (lowercased names and uuids); `api.names.match(name: str, terms) -> str | None` (the term hit); `BLOCKLIST_PATH`, `ALLOWLIST_PATH` module constants. `derive_blocked.update_blocked(conn, merges) -> int` rewrites `blocked_players` (players.name + `merges.former_names(uuid)` from Task 4; until Task 4 lands, players.name only).
- `api.scope.is_blocked(conn, uuid) -> bool`.

- [ ] **Step 1: Failing tests:**
  - `test_normalize`: `normalize("N_1gg3r") == "niger"`, `normalize("Fa$$ot") == "fasot"`, `normalize("aaBB__cc") == "abc"`.
  - `test_match_and_allowlist`: terms from a tmp file `["badword"]`; `match("xX_b4dw0rd_Xx", terms) == "badword"`; allowlisted name not blocked by `update_blocked`.
  - `test_blocked_hidden_everywhere`: a REAL player named `b4dword` on cmp: absent from `/leaderboard` (with and without `include_bots`), `/search`, `/firsts`; `/player/b4dword` → 404; `/compare` with them → 404; `stat_totals_current.total` still includes their value.
  - `test_block_list_edit_applies_next_run`: add the term after one run, run again → blocked; remove it → visible.
  - `test_blocked_former_name` (after Task 4; mark in Task 4): a player whose `aka` name matches is blocked.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Collapse in `normalize` applies after the leet map. `_scope_sql` adds `AND NOT EXISTS (SELECT 1 FROM blocked_players _blk WHERE _blk.uuid={a}.{uuid_col})` when `blocked`; add `_blk` to `_RESERVED`. `update_blocked` = DELETE + INSERT inside derive's transaction. `tools/check_blocklist.py [--db PATH]`: read-only, prints `name  uuid  term` for every match among `players.name`, former names and distinct `scoreboard_snapshots.raw_name`, and the allowlisted ones separately.
- [ ] **Step 4: Seed** `name_blocklist.txt`: header comment (how to edit, allowlist file, takes effect next snapshot run), then racial/ethnic, homophobic/transphobic and ableist slurs and common spellings (LDNOOBW narrowed to slurs; no plain profanity). Terms short enough to hit innocent names are listed only in forms that need them (check with `tools/check_blocklist.py` against the test DB names).
- [ ] **Step 5:** full suite passes.
- [ ] **Step 6: Commit** `feat(stats): slur filter on player names (blocked_players, hard block)`.

### Task 4: `renames` in `player_merges.json`, former names, `aka`

**Files:**
- Modify: `api/merges.py`, `api/routes/player.py` (`resolve_player`, `aka`), `player_merges.json`
- Test: `tests/test_player_merges.py`

**Interfaces:**
- Produces on `Merges`: `renames: tuple[Rename]` with `Rename(uuid: str, name: str (lower), from_ts: int|None, until_ts: int|None)`; `rename_target(name: str, at: int) -> str | None` (date-bounded; `until` inclusive to end of that UTC day); `former_names(uuid: str) -> list[str]` (original-case names from renames for this uuid and merges `names` of accounts merged into it, deduped, sorted case-insensitively).
- Parse errors (ValueError naming the entry): bad uuid, empty names, bad date, `from > until`, a name in both a rename and a merge, one name in two renames with overlapping dates.

- [ ] **Step 1: Failing tests:** parse a dict with one rename + one merge; `rename_target("notquiterat_", ts_2021) == U`; outside bounds → None; error cases above raise; `/player/NotQuiteRat_` resolves to U and the response has `"aka": ["NotQuiteRat_"]`; a merge target's `aka` includes the from-account's names; add `test_blocked_former_name` from Task 3.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement**; keep original-case names for `aka`. `resolve_player` checks renames (any date) after uuid and merge names. Wire `former_names` into `update_blocked`.
- [ ] **Step 4: Fill `player_merges.json`** (keep `_comment`, extend it with the `renames` format): renames bb8d717d-1e2f-4204-8ea5-88c90a60bca4 ← `NotQuiteRat_`; 5f2a15be-cdcc-49a6-a74a-1b31e0562786 ← `Q1306`; 5e6b97fb-3ee3-47f6-9a55-a8058a91eed6 ← `EulerGauss`; 38e6bba5-6c61-45fb-bdfb-d6f0eb3e22a2 ← `Obi0081_`, `Obi81`; 69dcb78d-ee6c-4400-99f8-ffda005cdaca ← `lukepourquoi`. Merges: c273fdfb-2181-47be-b174-6d8f20489b1b → 76e49874-1786-4d4c-9527-4dfd741c989d (`NotAless50_`); 13b1d964-7abf-456e-a928-4772ece0f5bd → 79c7e2d6-2486-43de-a959-0c68d0963496 (`wRongo_`); 6920e60b-012f-4b66-a32c-1c88aa71e879 → a53a264c-a7e2-4b01-85e2-5afa16043bfb (`qstrql`). Each with a `note`. Validate: `python -c "import sys; sys.path[:0]=['.']; from api.merges import load_merges; print(load_merges())"`.
- [ ] **Step 5:** full suite passes.
- [ ] **Step 6: Commit** `feat(stats): renames list, former names (aka) on /player`.

### Task 5: Rename fold and live/import resolution

**Files:**
- Modify: `bot/merge_players.py` (`apply_pending_merges` also folds pending renames; `_merge_scoreboards` accepts `a=None`), `bot/snapshot.py` (`run_scoreboard_snapshot` resolution), `tools/import_backups.py` (`_scoreboard_rows`, `_resolve_names`)
- Test: `tests/test_player_merges.py`, `tests/test_import_backups.py`

**Interfaces:**
- Consumes: Task 4 `Merges.renames`, `rename_target`.
- Produces: meta `renamed:<uuid>:<name lower>` = `"1"` per folded rename; resolution order everywhere: rename (date-bounded at the row's `at`) → usercache/players → merges target.

- [ ] **Step 1: Failing tests:**
  - `test_rename_fold_sums_unresolved_rows`: history rows: uuid U `NotQuiteRat` 13132 at T1; unresolved `NotQuiteRat_` 1,334,970 at T1, 9,371,069 at T2, 0 at T3; U 10,304,631 at T3. After a snapshot with the rename listed: U's dig-pickaxe timeline = `[(T1, 1348102), (T2, 9384201), (T3, 10304631)]`; no unresolved `NotQuiteRat_` rows left; `scoreboard_current` has no `unresolved:NotQuiteRat_`; meta key set; second run folds nothing.
  - `test_rename_date_bounds`: rows outside `from`/`until` stay unresolved.
  - `test_live_snapshot_resolves_rename`: scoreboard.dat has `NotQuiteRat_` 5 and `NotQuiteRat` 10 → `scoreboard_current` U = 15.
  - `test_import_resolves_rename`: a backup scoreboard with `Q1306` (no usercache entry) → rows under the rename's uuid.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** Rename fold = `_merge_scoreboards(conn, None, uuid, {name}, b_name)` restricted to the rename's date bounds (pass `from_ts`/`until_ts`; only unresolved rows inside the bounds are summed and deleted; `scoreboard_current` `unresolved:<name>` rows are added only when the bounds include the latest live run). Same backup + one transaction as merges; totals rebuilt for touched servers.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): fold and resolve renamed players' old scoreboard names`.

### Task 6: `GET /rankings`

**Files:**
- Create: `api/routes/rankings.py`; Modify: `api/main.py` (include router)
- Test: `tests/test_api_rankings.py`

**Interfaces:**
- `GET /rankings?by=top1|firsts|badges&scope=all&include_bots=false&limit=50&offset=0` → `{"by": str, "total_count": int, "rows": [{"rank", "uuid", "name", "value"}]}`; bad `by` → 400 like other ValueErrors. Shares the rank/tie code with `/leaderboard` (extract `rank_rows(vals: dict[str,int]) -> list[tuple[str,int,int]]` into `api/routes/leaderboard.py`).

- [ ] **Step 1: Failing tests:** three real players A, B, C on smp (teamed) and cmp:
  - top1: A highest in 2 stat keys, B in 1 key + tie with A in 1 key, scoreboard objective won by C → A=3, B=2, C=1; mirror objective wins not counted; fake player never appears even with `include_bots=true`.
  - firsts: rows with `earliest_known=1` not counted; approximate counted.
  - badges: distinct badge keys, server-NULL badges counted; scope=cmp counts only cmp + NULL.
  - ties share rank; `limit`/`offset`; `total_count`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement.** top1: SQL with `RANK() OVER (PARTITION BY key ORDER BY summed DESC)` over `stat_current` (scope-summed per uuid, value > 0) plus the same over `scoreboard_current`; filters from `stat_filter_sql`/`scoreboard_filter_sql(..., is_current=True)`; then `AND NOT fake` always. `@cached`.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): /rankings (top1, firsts, badges)`.

### Task 7: `/totals` per-line starts

**Files:** Modify: `api/routes/series.py` (`totals`); Test: `tests/test_api_series.py`

**Interfaces:** response adds `"starts": {scope: int|null}`; top-level `effective_from` = min of non-null starts (or the union's when all null); `to` unchanged.

- [ ] **Step 1: Failing tests:** smp tracked from T0 (import-like early run), cmp from T1 > T0: `/totals?scopes=smp,cmp&range=all` → `starts == {"smp": T0, "cmp": T1}`, `effective_from == T0`, smp series first point at T0, cmp's at T1. A scope with nothing in the window → `starts[s] is None`, series `[]`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement:** `parse_range` per scope; per-scope `for_values()`; events read once for the union over `[min start, to]`, each line downsampled from its own start.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): /totals per-scope starts`.

### Task 8: `/leaderboard?q=`

**Files:** Modify: `api/routes/leaderboard.py`; Test: `tests/test_api_leaderboard.py`

- [ ] **Step 1: Failing tests:** board of 4 players; `q="al ice"` matches `Al_ice` only, keeps its real `rank` (e.g. 3), `total_count == 1`; `q="ZZZ"` → `total_count 0, rows []`; `q` matches a former name (aka) too.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement:** rank all, then filter by `q.lower().replace(" ", "_")` substring of name or any `former_names` (lowercased); names for the filter fetched for all ranked uuids in one query.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): /leaderboard name filter`.

### Task 9: `/search` spaces and former names

**Files:** Modify: `api/routes/search.py`; Test: `tests/test_api_search.py`

- [ ] **Step 1: Failing tests:** `q="redstone block"`, `"Redstone Block"`, `"redstone_block"` all return `minecraft:redstone_block` first; objective `dig_all` found by `"dig all"` (only `_` equals a space, `-` does not); player found by an aka name, result shows the current name.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement:** normalise query and indexed texts with `.lower().replace(" ", "_")`; player texts = current name + former names.
- [ ] **Step 4:** full suite passes.
- [ ] **Step 5: Commit** `feat(stats): /search matches spaces as underscores and former names`.

### Task 10: Docs and deploy notes

**Files:** Modify: `docs/STATS_API.md`, `CLAUDE.md`, `docs/STATS_DATABASE.md`, `tools/README.md`, `deploy/update.sh` (treat `name_blocklist.txt`/`name_allowlist.txt` changes like `player_merges.json`, and add them to `SPARSE_PATTERNS` if needed); `TEMP_INFO.md` (untracked) with the server steps.

- [ ] **Step 1:** STATS_API.md: bots section (fake rule, `has_bots`, slur block, totals keep blocked numbers), `/search include_bots` + space rule + aka matches, `/player` `is_fake`/`is_bot`/`aka` + 404 for blocked, `/compare.players[]` flags, `/rankings` (full shape), `/totals.starts` and the new `effective_from` meaning, `/leaderboard q`, merge rule (sum, why), and a "What changed (round 2)" list per prompt item with the deviations (merge sum; `effective_from` = earliest start; fake firsts re-seeded; v3 detection limit).
- [ ] **Step 2:** CLAUDE.md: fake-player rule replaces the old "one rule" paragraph's scope (v3 = bot on every server), slur filter, renames, new files.
- [ ] **Step 3:** `python -m pytest tests -q` → all pass; `bash -n deploy/update.sh`.
- [ ] **Step 4: Commit** `docs(stats): round 2 API contract and operator notes`.
