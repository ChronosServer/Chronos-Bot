# Old world backup import: design

Status: **approved** by the owner · 2026-11-24

## Intent
Chronos SMP has run as one continuous world since 2021 (started on 1.12 and updated
version by version). Stats tracking only began on 2026-11-24. The owner has full SMP
world backups from 1.16, 1.17, 1.18, 1.20 and 1.21 on the server; the 1.12 backup is lost
and the backups are not archives. Importing them gives the Stats pages years of history:
graphs, "gained over 1 year", and real dated firsts and milestones. It must not require
opening or upgrading the worlds in Minecraft.

## Goals
- A one-time, repeatable-safe command-line import of backup world folders into the stats
  DB, as earlier points on the existing timeline of a tracked server (SMP).
- The date, version and file layout of each backup are read from the backup itself.
- Afterwards, the derived data reflects the longer history.

## Non-goals
- 1.12 support (no backup exists, and it would need a separate pre-1.13 stat translator).
- Activity, heatmap and pulse clock before deploy: backups months apart can't say who was
  online when.
- CMP backups (none exist). The tool still takes `--server <key>` for any tracked server.

## Anti-goals
- **Splitting a stat in two** because a backup uses an old name (e.g. `play_one_minute`
  vs `play_time`).
- **Touching live values:** `stat_current` / `scoreboard_current` are only ever written
  by the live snapshot.
- **Duplicating history on a rerun.**
- **Presenting approximate dates as exact:** a crossing found between two backups is
  dated "by <later backup date>", never as a precise moment.

## Approach (chosen)
A command-line tool, run by hand on the server.

Rejected alternatives:
- **A Discord command:** long DB-heavy job inside the bot, with server paths typed into
  chat.
- **Making the 15-min snapshot read legacy layouts:** one-time code in a forever path.

## Design

### Files
- `tools/import_backups.py`: the tool.
- `tools/stat_renames.json`: old key → current key, per category. It's editable data, not
  code.
- `tools/README.md`: usage docs, linked from the repo README and `docs/STATS_DATABASE.md`.

### Usage
```
python3 tools/import_backups.py --server smp [--dry-run] [--no-backup]
                                [--date <world_dir>=YYYY-MM-DD ...] <world_dir> [<world_dir> ...]
```

### Per backup world folder
- **Date:**
  - from `level.dat` → `Data.LastPlayed` (real-world ms, stored inside the file, so it
    survives copying);
  - `--date` overrides it;
  - a folder without a readable date is refused.
- **Version:** `Data.Version.Name` / `DataVersion`, for the report and the rename choice.
- **Stats:** `<world>/stats/*.json` (≤1.21) or `<world>/players/stats/*.json` (26.x).
  Only the modern nested format (1.13+) is imported. Pre-1.13 flat files (players who
  never logged in after 1.13) are skipped one by one and counted in the report; a backup
  whose stat files are all pre-1.13 is refused with a clear message.
- **Scoreboard:** `<world>/data/scoreboard.dat` or `<world>/data/minecraft/scoreboard.dat`.
  Same NBT reader as the live snapshot.
- **Names:** `usercache.json` next to the world folder, if present. It only fills
  players whose name is unknown, never overwriting a newer name.

### Importing
- **Order:** backups are processed oldest → newest. Two backups with the same date: the
  later one wins (warn). A backup dated on or after the server's first live snapshot is
  refused (it would rewrite the live timeline).
- **Renames:** each stat key is passed through `stat_renames.json`. The defaults cover
  the known renames from 1.16→26.x, e.g. `minecraft:custom/minecraft:play_one_minute` →
  `minecraft:play_time`, `grass_path` → `dirt_path`, `grass` → `short_grass`. The builder
  compiles the list from the Minecraft changelogs and verifies it against the real
  backups during the dry run.
  - Keys that exist neither in the rename table nor anywhere in the live DB are reported
    as "unknown". They are imported unchanged unless `--skip-unknown`.
  - If an old and a new key both map to the same target in one file, their values are
    summed.
- **History rows:**
  - Rows go into `stat_snapshots` / `scoreboard_snapshots` at the backup's date.
  - Change-only against the previous imported point for that (server, player, key): the
    first backup writes everything, and later backups write only what changed.
  - Vanilla-mirror objectives are skipped (the same dictionary rule as live), and so are
    fake/unresolved participants (the same rule as live).
- **Run records:** each backup records a `snapshot_runs` row (`kind='import'`, `ok`,
  `entries_read`, `rows_written`) at its date.
  - Tracking start becomes the oldest backup's date: `tracking_started_at` is computed
    over `kind IN ('stats','import')` with `entries_read>0`.
  - An `imports` table (world path, level name, last_played, data_version, imported_at,
    rows) makes reruns skip backups already imported.

### Rebuilding derived data after import (one pass, at the end)
- **Totals history:** reset the `totals_backfilled` marker and rebuild with the existing
  backfill, now starting at the oldest backup.
- **Firsts and milestones** are rebuilt for the imported server.
  - Deleted: rows with `earliest_known=1`, plus rows with the new
    `approximate=1` flag.
  - Kept: real live rows (`earliest_known=0, approximate=0`).
  - Already true at the oldest backup → `earliest_known=1` (as today).
  - Crossed between two consecutive points (backup→backup, or last backup→first live
    snapshot) → dated at the later point with `approximate=1, earliest_known=0`.
  - New column `approximate INTEGER NOT NULL DEFAULT 0` on `firsts` and `milestones`, via
    migration v3.
- **Badges:** recomputed. Approximate firsts count toward the Firsts badges, since they
  are real, observed firsts.
- **Rivalries / spotlight:** they refresh on the next live run, as usual.

### API and website
- `/firsts`, `/milestones` and the `/player` firsts/milestones lists gain `approximate`.
- `STATS_API.md` says the website shows these as "by <date>". `earliest_known` keeps
  its meaning.

### Safety
- Before writing, the tool makes an online backup of the DB file (SQLite backup API,
  next to the DB, timestamped) unless `--no-backup`.
- The tool refuses to run while a snapshot is in progress (DB write lock check). The docs
  say to stop the timer and snapshot service first and start them again afterwards.
- `--dry-run` writes nothing and prints, per backup: path, level name, version, date,
  stats layout, player count, the rows that would be written, renamed keys hit, unknown
  keys, and the scoreboard objective count.

### Tests
Synthetic backup worlds in tmp dirs:
- one `stats/` layout with `play_one_minute` and `grass_path`;
- one `players/stats/` layout;
- `data/scoreboard.dat` vs `data/minecraft/scoreboard.dat`;
- `level.dat` written with nbt.

Cases:
- renames merge into the current key;
- change-only across backups;
- reruns skip;
- a backup dated after live tracking is refused;
- pre-1.13 files skipped and counted; an all-pre-1.13 backup refused;
- tracking start moves back;
- totals rebuilt with no cliff;
- milestones crossed between backups are dated at the later backup with
  `approximate=1`;
- seeded rows rebuilt and live real rows untouched;
- dry run writes nothing;
- the DB backup file is created.

## Left to the builder
- The full rename table: compiled from changelogs and checked against the owner's
  backups via `--dry-run`.
- Output formatting.
- Exactly how the derived rebuild reuses the existing derive code (it must reuse it, not
  copy it).
