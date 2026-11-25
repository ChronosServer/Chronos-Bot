"""settings snapshot.ignored_stat_keys: air isn't stored, stored rows go once."""
import sqlite3

U1 = "11111111-1111-1111-1111-111111111111"


def q(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def stats(air):
    return {"minecraft:used": {"minecraft:air": air, "minecraft:bread": 3},
            "minecraft:crafted": {"minecraft:air": 0}}


def test_air_never_stored_and_old_rows_deleted_once(world, run_snapshot):
    world.set_usercache("cmp", {U1: "Alice"})
    world.add_stats("cmp", U1, stats(8))
    tracking = world.config["stats_tracking"]
    tracking.setdefault("snapshot", {})["ignored_stat_keys"] = []   # old behaviour
    run_snapshot(1000)
    assert q(world, "SELECT COUNT(*) FROM stat_current WHERE stat_key='minecraft:air'") == [(2,)]
    assert q(world, "SELECT COUNT(*) FROM stat_totals WHERE stat_key='minecraft:air'") != [(0,)]

    del tracking["snapshot"]["ignored_stat_keys"]                     # default: air ignored
    world.add_stats("cmp", U1, stats(9))
    run_snapshot(2000)
    for tbl in ("stat_current", "stat_snapshots", "stat_totals", "stat_totals_current",
                "firsts", "milestones"):
        assert q(world, f"SELECT COUNT(*) FROM {tbl} WHERE stat_key='minecraft:air'") == [(0,)], tbl
    assert q(world, "SELECT value FROM stat_current WHERE stat_key='minecraft:bread'") == [(3,)]
    assert q(world, "SELECT value FROM meta WHERE key='ignored_stat_keys'") == [
        ('["minecraft:air", "minecraft:cave_air", "minecraft:void_air"]',)]
