# Backup Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A command-line tool that imports old SMP world backups (1.16–1.21 and 26.x layouts) into the stats DB as earlier points on the live timeline, then rebuilds the derived data so history, totals, firsts and milestones reach back to the oldest backup.

**Architecture:** `tools/import_backups.py` reads each backup's `level.dat` / stats / scoreboard with the same NBT and JSON readers the live snapshot uses. It maps renamed keys through `tools/stat_renames.json` and writes change-only history rows at each backup's date. It records `kind='import'` runs and an `imports` table, and finally calls a rebuild in the derive package that re-dates firsts and milestones between points and rebuilds totals history.

**Tech Stack:** Python 3.13/3.14, sqlite3, nbt; the existing `bot/` and `api/` modules.

**Spec:** `docs/superpowers/specs/2026-11-24-backup-import-design.md`

## Global Constraints

- **Never write `stat_current` or `scoreboard_current`.** Only history tables, `snapshot_runs`, `imports`, `players` (unknown names only) and derived tables.
- **Backup date** = `level.dat` → `Data.LastPlayed` (ms → s), overridable with `--date <world_dir>=YYYY-MM-DD` (00:00 UTC). No date → refuse that backup.
- **Refuse** a backup dated **on or after** the server's first live snapshot, i.e. `MIN(at)` of `snapshot_runs` with `kind='stats' AND ok=1 AND entries_read>0`. Before any live snapshot exists, nothing is refused.
- **Only the modern format.** A stats JSON without a top-level `"stats"` object (pre-1.13 flat `stat.*` keys) → refuse that backup with "pre-1.13 stats format is not supported".
- **Stats location:** `<world>/stats/` or `<world>/players/stats/`. **Scoreboard location:** `<world>/data/scoreboard.dat` or `<world>/data/minecraft/scoreboard.dat`.
- **Change-only:** a history row is written only when the value differs from the previous imported point for that (server, uuid, category, key). The first imported backup writes every non-zero value.
- **Same participant rules as live:**
  - vanilla-mirror objectives are skipped (current dictionary);
  - unresolved/fake scoreboard participants are stored as live does, with
    `participant_kind='unresolved'`;
  - bot rules apply at read time as today.
- **Tracking start** (both `derive_common` and API/meta) = `MIN(at)` over `snapshot_runs` with `kind IN ('stats','import') AND ok=1 AND entries_read>0`.
- **New columns** via migration v3: `firsts.approximate`, `milestones.approximate` (`INTEGER NOT NULL DEFAULT 0`), plus the table `imports(world_path TEXT, level_name TEXT, server_key TEXT, last_played INTEGER, data_version INTEGER, imported_at INTEGER, rows_written INTEGER, PRIMARY KEY(server_key, last_played))`.
- **DB backup** before any write unless `--no-backup`: SQLite online backup to `<db_dir>/pre-import-YYYYmmdd-HHMMSS.db`.
- **Busy DB → abort.** Acquire the write lock with `BEGIN IMMEDIATE` (timeout 5 s). If it's busy, abort with "a snapshot is running — stop chronos-stats-snapshot.timer and .service first".
- **Tuning** comes from `api/settings.py` (config refactor already landed). Don't add new hardcoded tuning values; the rename table lives in `tools/stat_renames.json`.
- **Tests never touch the network.** The suite stays at zero warnings.

## Review Focus

1. **Two backups resolving to the same date** (copied worlds, or a `--date` collision): the later in argument order wins, with a warning, and no duplicate rows. Test in Task 2.
2. **A key renamed with both old and new names in one stats file** (e.g. a world opened briefly in a mid version): values are summed into the target key, not overwritten. Test in Task 2.
3. **Rerunning the full import after a successful one:** zero new rows, no new imports rows, and the derived rebuild is a no-op (counts unchanged). Test in Task 3.
4. **A player present in an older backup but missing in a newer one** (stat file deleted): no history row means they keep their older value, never a fake drop to 0. Test in Task 2.
5. **A live milestone (`earliest_known=0, approximate=0`) for the same threshold as an approximate one found from backups:** the live row is kept and no duplicate is created (unique index). Test in Task 3.

---

## File structure

```
sql/migrations.py              MODIFY: v3 (approximate columns, imports table)
bot/derive_common.py           MODIFY: tracking_start includes kind='import'
api/timeq.py / api meta        MODIFY only if they compute tracking start themselves (they read meta: verify)
bot/derive_achievements.py     MODIFY: rebuild_from_points(); approximate rows; badges count approximate firsts
api/routes/feeds.py, player.py MODIFY: expose `approximate`
tools/__init__.py              CREATE (empty)
tools/import_backups.py        CREATE: CLI + reader + importer
tools/stat_renames.json        CREATE
tools/README.md                CREATE
tests/test_import_*.py         CREATE
README.md, docs/STATS_DATABASE.md, STATS_API.md, CLAUDE.md   MODIFY: links + `approximate`
```

### Task 1: Schema v3, tracking start over imports, `approximate` in the API

**Files:** Modify `sql/migrations.py`, `bot/derive_common.py` (tracking_start), any other `tracking_start`/`tracking_started_at` computation (grep), `api/routes/feeds.py`, `api/routes/player.py`, `STATS_API.md`. Test: `tests/test_import_schema.py`.

**Interfaces:**
- Produces:
  - `migrate()` brings DBs to `user_version` 3 with the columns and table in Global Constraints;
  - `tracking_start(conn, server_key)` counts `kind IN ('stats','import')`;
  - every firsts/milestones dict returned by `/firsts`, `/milestones` and `/player` gains `"approximate": 0|1`.

- [ ] **Step 1: Failing tests.**
  - `test_migrate_v3_columns_and_imports_table`: fresh DB has `user_version == 3`, `approximate` in both `PRAGMA table_info`, and the `imports` table exists.
  - `test_migrate_v2_db_to_v3`: DB at v2 with rows; after migrate the rows are kept and `approximate == 0`.
  - `test_tracking_start_counts_import_runs`: insert a `kind='import', ok=1, entries_read=5` run at t=100 and a stats run at 1000, so `tracking_start == 100`; the meta `tracking_started_at:smp` written by derive == 100.
  - `test_api_exposes_approximate`: `/firsts`, `/milestones` and `/player` rows contain `approximate`.
- [ ] **Step 2: Run, expect FAIL.** `python -m pytest tests/test_import_schema.py -q`
- [ ] **Step 3: Implement.** Update `STATS_API.md`: `approximate: 1` → show "by <date>". Update the test asserting `user_version == 2` to 3.
- [ ] **Step 4: Full suite PASS.** `python -m pytest tests -q`
- [ ] **Step 5: Commit** `stats: schema v3 (approximate, imports), tracking start includes imports`

### Task 2: The import tool (read, map, write history)

**Files:** Create `tools/__init__.py`, `tools/import_backups.py`, `tools/stat_renames.json`. Test: `tests/test_import_backups.py`, plus a fixture builder for backup worlds in `tests/conftest.py`: `make_backup(tmp, *, layout="old"|"new", last_played_ms, stats: {uuid: stats_dict}, scoreboard=None, usercache=None) -> Path`.

**Interfaces:**
- Consumes:
  - `snapshot.connect_db`, `snapshot.load_config`, `snapshot.load_usercache`;
  - the snapshot NBT helpers (`nbt_list`, `nbt_val`) and `fetch_dictionary` or the cached dictionary;
  - `api/settings.load_settings`;
  - Task 1's tables.
- Produces:
  - `read_backup(world_dir: Path, date_override: int | None) -> Backup`
    (`Backup`: dataclass with `path, level_name, version_name, data_version, at, stats_dir, scoreboard_path, usercache`, where `usercache` is `{uuid: name}`);
    raises `ValueError` with a reason when refused;
  - `load_renames(path) -> dict[str, dict[str, str]]` (category → {old_key: new_key});
  - `import_backups(conn, server_key, backups: list[Backup], renames, dictionary, dry_run: bool) -> list[ImportResult]`
    (`ImportResult`: `path, at, players, rows_stats, rows_scoreboard, renamed_hits: dict, unknown_keys: set, skipped_reason: str | None`);
  - `main(argv) -> int` with flags `--server` (required), `--dry-run`, `--no-backup`, `--skip-unknown`, `--date DIR=YYYY-MM-DD` (repeatable), `--config` (default `config.json`); after a non-dry import it calls Task 3's `rebuild_from_points(conn, server_key, settings)` (a no-op stub until Task 3).
- `stat_renames.json` shape: `{"minecraft:custom": {"minecraft:play_one_minute": "minecraft:play_time"}, "*": {"minecraft:grass_path": "minecraft:dirt_path", "minecraft:grass": "minecraft:short_grass"}}`.
  - `"*"` applies to every category.
  - Compile the 1.16→26.x renames from the changelogs, at least: play_one_minute→play_time, grass_path→dirt_path, grass→short_grass. Note each entry's version in the report.
  - Unknown key = not in the rename table and not present as a key in `stat_current` for any server.

- [ ] **Step 1: Failing tests.**
  - `test_read_backup_old_layout`: `stats/` plus `data/scoreboard.dat`, with the date from LastPlayed.
  - `test_read_backup_new_layout`: `players/stats/` plus `data/minecraft/scoreboard.dat`.
  - `test_date_override`.
  - `test_missing_date_refused`.
  - `test_pre113_format_refused`.
  - `test_backup_after_live_start_refused`.
  - `test_renames_merge_into_current_key`: play_one_minute 100 → a play_time row of 100.
  - `test_old_and_new_key_same_file_summed` (Review Focus 2).
  - `test_change_only_across_backups`: 3 backups, unchanged values written once.
  - `test_same_date_later_wins_warns` (Review Focus 1).
  - `test_missing_player_in_newer_backup_keeps_value` (Review Focus 4).
  - `test_rerun_skips_imported`: the imports table makes a rerun write 0 rows.
  - `test_dry_run_writes_nothing`.
  - `test_db_backup_created` / `test_no_backup_flag`.
  - `test_busy_db_aborts`: another connection holds `BEGIN IMMEDIATE`.
  - `test_mirror_objectives_skipped`.
  - `test_unknown_names_filled_not_overwritten`.
  - `test_live_current_tables_untouched`: the checksum of stat_current is unchanged.
  - `test_unknown_keys_reported_and_skip_flag`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
  - Process backups oldest first.
  - Previous-point values come from the last imported row ≤ that point, reading history with `INDEXED BY idx_stat_point` / `idx_scoreboard_point`.
  - Write one `snapshot_runs` row (`kind='import'`) per backup, at its date.
- [ ] **Step 4: Full suite PASS.**
- [ ] **Step 5: Commit** `tools: import_backups — read old world backups into history`

### Task 3: Rebuild derived data from points, docs

**Files:** Modify `bot/derive_achievements.py` (and `bot/derive.py` if the totals rebuild needs a public hook), `tools/import_backups.py` (call the rebuild), docs. Create `tools/README.md`. Test: `tests/test_import_rebuild.py`.

**Interfaces:**
- Consumes: Task 2's `import_backups`, `derive.backfill_totals` (or its marker reset), and the existing badge derivation.
- Produces: `rebuild_from_points(conn, server_key, settings) -> dict` (counts of firsts/milestones/badges/totals rows written), in the derive package. Steps:
  - **Points:** the ordered distinct `at` of `snapshot_runs` with `kind='import'` for the server, plus the first live stats run's `at` (if any).
  - **Delete** that server's firsts/milestones rows with `earliest_known=1` or `approximate=1`.
  - **Oldest point:** everything true there → `earliest_known=1`, the same as the existing seeding logic, but evaluated at the oldest point via value-at-T, not current.
  - **Each consecutive pair (p, q):** crossings with value_at(p) < threshold ≤ value_at(q), and keys with no counted holder > 0 at p but some at q (firsts: the top counted holder at q) → rows at `q` with `approximate=1, earliest_known=0`.
  - Use `INSERT OR IGNORE`, so live real rows win (Review Focus 5).
  - **Totals:** reset the `totals_backfilled` marker and run the existing backfill.
  - **Badges:** run the existing badge stage. Firsts badges count `approximate=1` rows; seeded `earliest_known=1` rows still don't count.
  - Must reuse the existing milestone-rule / firsts / badge code paths, not copy them.

- [ ] **Step 1: Failing tests.**
  - `test_tracking_start_moves_back`.
  - `test_milestone_between_backups_dated_at_later_point_approximate`: stone 900 @ backup1 → 1500 @ backup2, so threshold 1000 is at backup2's date with approximate=1.
  - `test_true_at_oldest_is_earliest_known`.
  - `test_crossing_between_last_backup_and_first_live_run_dated_at_live_run`.
  - `test_first_between_backups_approximate`.
  - `test_live_real_rows_untouched` (Review Focus 5).
  - `test_totals_history_from_oldest_backup_no_cliff`.
  - `test_approximate_first_counts_for_firsts_badge`.
  - `test_full_rerun_is_noop` (Review Focus 3).
  - `test_main_end_to_end_cli`: `main([...])` on two backups returns 0 and prints the summary.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**, plus the docs:
  - **`tools/README.md`:** what it does, stop the timer + service first, `--dry-run` first, every flag, example output, the rename table and how to extend it, how to undo (restore the pre-import DB backup), and that activity before deploy isn't reconstructed.
  - **Links:** add from `README.md` (stats section) and `docs/STATS_DATABASE.md` (new section "Importing old world backups").
  - **`CLAUDE.md`:** the tools folder plus the `approximate` meaning.
- [ ] **Step 4: Full suite PASS.**
- [ ] **Step 5: Commit** `tools: rebuild derived data after backup import; docs`
