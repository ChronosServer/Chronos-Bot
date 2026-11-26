# Importing old world backups (`tools/import_backups.py`)

The stats database only knows what the live snapshot has seen since tracking began.
This tool reads old **backup world folders** (1.16 – 1.21, and the 26.x layout) and adds
them to the database as earlier points on the same timeline. Afterwards the Stats
pages show years of history: graphs and totals that start at the oldest backup, "gained
over 1 year", and firsts and milestones dated by the backups.

You run it by hand on the server, once per batch of backups. Running it again is safe,
because backups that are already imported are skipped.

## What it does

For each backup folder:

1. It reads the date from `level.dat` (`Data.LastPlayed`), or from `--date` if you give one.
   It also reads the version and level name, but only for the report.
2. It reads the stat files from `<world>/stats/` (1.21 and older) or `<world>/players/stats/`
   (26.x). It reads the scoreboard from `<world>/data/scoreboard.dat` or
   `<world>/data/minecraft/scoreboard.dat`. These are the same readers the live snapshot
   uses.
3. It maps old stat keys to today's names with `tools/stat_renames.json`, so
   `play_one_minute` becomes `play_time` and `grass_path` becomes `dirt_path`. If one file
   holds both an old and a new name, the values are added together.
4. It writes **change-only** history rows at the backup's date, the same way the live
   snapshot stores history. The first backup writes every value. Each later backup writes
   only the values that changed since the backup before it.
5. It records each backup as a `snapshot_runs` row with `kind='import'` and as a row in
   the `imports` table.

When at least one backup was imported, it then **rebuilds the derived data** for that
server (`rebuild_from_points` in `bot/derive_achievements.py`):

- **Tracking start** moves back to the oldest backup.
- **Server totals history** is rebuilt from all history, so it starts at the oldest backup
  with no jump on deploy day.
  - The first live snapshot counts as a full look at the world. A player or score that
    appears only in backups, and not in the live data, stops counting toward totals from
    that date on. Their own history rows are kept.
  - An objective that exists only in backups drops to a total of 0 at that date.
- **Firsts and milestones** are recomputed over the points (each backup, plus the first
  live snapshot):
  - Anything already true at the oldest backup is "earliest known", with no date.
  - Anything that happened between two points is dated at the later point and marked
    `approximate`. The website shows it as "by <date>".
  - Firsts and milestones the live snapshot saw happen are never changed.
- **Badges** are awarded again. Approximate firsts count toward the Firsts badges.

## What it doesn't do

- **No activity before deploy.** Backups months apart can't say who was online when, so
  the activity heatmap, pulse clock and "active" badges start at deploy, as before.
- **Objectives that exist only in old backups get no `scoreboard_meta` row.** Their scores
  are imported into history, but `scoreboard_meta` belongs to the live snapshot. These
  objectives still show up in history queries.
- It never writes `stat_current` or `scoreboard_current`. Live values belong to the live
  snapshot.
- It refuses a backup dated **on or after** the first live snapshot, because that would
  rewrite the live timeline.
- **Pre-1.13 stat files are skipped.** SMP started on 1.12, so players who never logged
  in after 1.13 keep an old flat-format `stats/<uuid>.json` in every backup. Those files
  are skipped one by one and counted (`pre-1.13 stat files skipped: N` in the report);
  the rest of the backup is imported. A backup whose stat files are *all* pre-1.13 is
  refused.
- Rivalries and the spotlight refresh on the next live run, as usual.
- The rebuild deletes and re-inserts firsts and milestones, so their row ids change. If a
  client is paging through `/firsts` or `/milestones` while the rebuild runs, its cursor
  may shift. Reloading the page fixes it.

## Before you start

1. **Stop the live snapshot.** The tool won't run while a snapshot holds the database. It
   gives up after 5 seconds with "a snapshot is running".

   ```bash
   systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service
   ```

2. Run the import **after** the live snapshot has run at least once, which is already the
   case on the live server. If no live snapshot exists yet, changes between the last backup
   and the first live run can't be marked approximate, and the tool warns you about this.
3. The API can keep running. It only reads.

When you're done, start the timer again:

```bash
systemctl start chronos-stats-snapshot.timer
```

## Always do a dry run first

```bash
cd /opt/chronos/Chronos-Bot
/opt/python/bin/python3 tools/import_backups.py --server smp --dry-run /path/to/backup-1.16/world
```

`--dry-run` reads everything and prints what it would do, but writes nothing (and makes
no DB backup). Here is what the output looks like for one backup:

```
DRY RUN, nothing written - server smp
/backups/smp-1.16/world
  level 'world', version 1.16.5 (data 2586), date 2021-06-01 18:04 UTC, layout old (stats)
  players 812; would write 401233 stat rows, 2210 scoreboard rows
  pre-1.13 stat files skipped: 37
  scoreboard objectives 9105; mirrors skipped by dictionary: 8990
  probable mirrors skipped: 3 (first 20: kb-old_mob, m-grass_path, u-scute)
  renamed keys: minecraft:custom/minecraft:play_one_minute x812, minecraft:mined/minecraft:grass_path x95
  unknown keys (2): minecraft:mined/minecraft:weird_block, ...
  old names summed into the current name (2): LordHamster42 -> hampter42, Obi81 -> ObiWan8_
total: 1 to import, 0 skipped, 0 refused; rows 401233 stats, 2210 scoreboard
```

What to check:

- **date:** Is it roughly when the backup was taken? If not (for example, a copied world
  that was opened again later), pass `--date`.
- **layout:** `old` (`stats/`) or `new` (`players/stats/`).
- **players / rows:** Row counts should get much smaller for each backup after the first
  (change-only). Counts that stay huge for every backup usually mean renames are missing.
- **pre-1.13 stat files skipped:** Old flat-format files of players who never logged in
  after 1.13. Normal for SMP (it started on 1.12); they hold nothing importable.
- **mirrors skipped by dictionary:** These are every-scoreboard's copies of vanilla stats,
  which are already stored from the stat files. Nearly all objectives should be here.
- **probable mirrors skipped:** These are objectives with a mirror-style prefix (`m-`,
  `u-`, `kb-`, …) that no dictionary knows about. They are skipped too. If one of them is
  a real objective, pass `--keep-prefixed`.
- **renamed keys:** The renames that were applied, with how many files had each one.
- **unknown keys:** Keys that are neither in the rename table nor anywhere in the live DB.
  These are often renamed keys missing from `stat_renames.json` (add them; see below), or
  removed blocks and items. They are imported unchanged unless you pass `--skip-unknown`.
- **old names summed into the current name:** Renamed players. Scoreboards key by name, so
  a renamed player's old name can still be on the board. Every name that maps to the same
  uuid (through any backup's `usercache.json` or the live player names) is added up under
  the player's current name, like the in-game `stattransfer`. A rename you know of that's
  missing here means its old name is in no usercache: it is imported as an unknown
  participant (not shown on the website). Accounts merged in `player_merges.json` (an alt
  counted as its main account) are summed the same way and appear in this list too, e.g.
  `LordHamster42 -> hampter42`.
- After writing, the import adds the 0 rows the first live snapshot implies for its server
  (a key in a backup but not in the live files), so delta leaderboards stay correct.
- **skipped / refused:** Each skipped or refused backup has its reason printed under it.

## Flags

| Flag | Meaning |
|---|---|
| `--server KEY` | Required. The tracked server the backups belong to, e.g. `smp`. |
| `<world_dir> ...` | One or more backup **world** folders (the folder that holds `level.dat`). They can be in any order, because they're processed oldest first. |
| `--dry-run` | Read and report, write nothing. |
| `--no-backup` | Skip the database backup made before writing (see "Undoing an import"). |
| `--date DIR=YYYY-MM-DD` | Override a backup's date (00:00 UTC). `DIR` must be written the same way as the folder argument. Repeat it for several folders. Use it when `level.dat` has no `LastPlayed` or the date is wrong. |
| `--skip-unknown` | Don't import unknown keys (see the dry run). |
| `--keep-prefixed` | Import objectives with a mirror-style prefix that no dictionary lists. |
| `--dictionary FILE` | An extra `dictionary-<version>.json` of mirror objective names. You can repeat it. The bundled `dictionaries/` folder is always used. |
| `--config PATH` | The `config.json` to read `stats_tracking` from. Default: the repo's `config.json`. |
| `--rebuild-only` | Import nothing and only rerun the derived-data rebuild for `--server`. You need this only if the rebuild after an import stopped because the DB was busy. |
| `--reimport-scoreboards` | Repair: rewrite the scoreboard rows of every backup already imported for `--server`, re-read from the folder recorded at import time, under the current participant rules (e.g. after the rename fix: every name of one player, matched by uuid through the backups' usercaches, is summed under the player's latest name, like the in-game `stattransfer`; the output lists each `old -> current` pair merged). Stat rows and live rows are untouched. Works with `--dry-run` (prints old -> new row counts per backup), makes the usual DB backup, then rebuilds. Every recorded folder must still be readable, or nothing changes. Takes no folders and no `--date`. |

Exit codes:

- `0`: everything went fine.
- `1`: a backup was refused, the database was busy, or the dictionary was missing.
- `2`: a bad argument or config, for example a bad `--date`, a `--date` for a folder you
  didn't list, no config or no `stats_tracking`, or a server that isn't in the DB yet.

Two backups with the same date: the one given **later** on the command line wins, and
the tool prints a warning. A folder that was already imported (at the same date, or at a
different date because of `--date`) is skipped with "already imported".

## Dictionaries (`dictionaries/`)

every-scoreboard creates a mirror objective for each vanilla stat. Old backups hold
mirrors of names that no longer exist today, so the tool skips the mirror names of
**every** dictionary in `dictionaries/` (1.16 – 26.2, from every-scoreboard) plus today's
dictionary.

To add a version:

1. In the every-scoreboard repo, run `python3 scripts/create.py --mcversion=<version> -c`.
2. Copy the resulting `dictionary-<version>.json` into this repo's `dictionaries/`.

## The rename table (`tools/stat_renames.json`)

It maps old keys to current keys, for each stat category:

```json
{
  "minecraft:custom": {"minecraft:play_one_minute": "minecraft:play_time"},
  "minecraft:killed": {"minecraft:boat": "minecraft:oak_boat"},
  "*": {"minecraft:grass_path": "minecraft:dirt_path"}
}
```

- A category key (`minecraft:custom`, `minecraft:mined`, …) applies only to that
  category. `"*"` applies to every category. Use a category when a name means different
  things in different categories. For example, `minecraft:boat` is the boat entity in
  `killed`, but it isn't an item name.
- Top-level keys that start with `_` are comments. `_versions` notes the release that
  renamed each key.

To extend it, add `"old_key": "new_key"` under the right category, add a `_versions`
note, and do the dry run again. The key should move from "unknown keys" to "renamed
keys". To check a rename, compare two bundled dictionaries: a key missing from the newer
one has usually been renamed.

## Undoing an import

Unless you passed `--no-backup`, the tool makes an online copy of the database **before
writing anything**: `pre-import-YYYYmmdd-HHMMSS.db`, next to the DB (the time is in UTC).
It prints the path. It makes no copy when nothing is going to be imported.

To undo, restore that file the same way as any backup (see
[docs/STATS_DATABASE.md](../docs/STATS_DATABASE.md), "Backup and restore"):

```bash
systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service chronos-stats-api
cp /opt/chronos/stats/pre-import-20261125-101500.db /opt/chronos/stats/chronos-stats.db
rm -f /opt/chronos/stats/chronos-stats.db-wal /opt/chronos/stats/chronos-stats.db-shm
systemctl start chronos-stats-snapshot.timer chronos-stats-api
```

Live snapshots taken after the import are lost when you restore. Undo soon after the
import, while the timer is still stopped.

## Worked example: the SMP backups (1.16, 1.17, 1.18, 1.20, 1.21)

The folder names below are examples. Use the real backup paths, and point each argument
at the world folder that contains `level.dat`.

```bash
systemctl stop chronos-stats-snapshot.timer chronos-stats-snapshot.service
cd /opt/chronos/Chronos-Bot

# 1. Dry run of all five at once
/opt/python/bin/python3 tools/import_backups.py --server smp --dry-run \
    /backups/smp-1.16/world-smp0 /backups/smp-1.17/world-smp0 /backups/smp-1.18/world-smp0 \
    /backups/smp-1.20/world-smp0 /backups/smp-1.21/world-smp0
```

Check that:

- each date matches when that backup was taken;
- the 1.16 backup shows `play_one_minute` and `grass_path` under "renamed keys";
- the row counts after the first backup are much smaller;
- "unknown keys" lists nothing that is really a rename.

Fix `stat_renames.json` or add `--date` as needed, and do the dry run again. Then:

```bash
# 2. The real import (same arguments, without --dry-run)
/opt/python/bin/python3 tools/import_backups.py --server smp \
    /backups/smp-1.16/world-smp0 /backups/smp-1.17/world-smp0 /backups/smp-1.18/world-smp0 \
    /backups/smp-1.20/world-smp0 /backups/smp-1.21/world-smp0
# prints "DB backup: ...pre-import-....db", the per-backup report, then
# "rebuild: {'points': 6, 'firsts': ..., 'milestones': ..., 'badges': ..., 'totals': ...} in N s"

# 3. Start the live snapshot again
systemctl start chronos-stats-snapshot.timer
```

The rebuild takes on the order of a minute on the live DB. In a synthetic test with 2,000
players, 100 keys and 5 backups (700k history rows), it took about 15 s. It holds the
write lock the whole time, which is why the timer must stay stopped.

If the rebuild reports "a snapshot is running", the import itself is already saved. Stop
the timer and the service, then run
`tools/import_backups.py --server smp --rebuild-only`.

To check the result, see `/api/stats/meta`: `tracking_started_at.smp` should now be the
1.16 backup's date.

## Item icons (`tools/render_items.py`)
A separate tool, documented in `docs/STATS_DATABASE.md` ("Player heads and item icons"):
renders every item id of `server.minecraft_version` from the client jar into
`<website_path>/cache/items/<id>.png`. Run it after each Minecraft version bump
(`--dry-run` first).
