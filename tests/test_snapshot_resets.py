"""Live snapshot: a score gone from PlayerScores (reset) is recorded as 0."""
import sqlite3
from pathlib import Path

import nbt.nbt as nbt

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


def rows(world, sql, *a):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def current(world):
    return dict(((p, o), v) for p, o, v in rows(
        world, "SELECT participant_key, objective, value FROM scoreboard_current"))


def setup(world, run_snapshot):
    world.set_usercache("cmp", {U1: "Alice", U2: "Bob"})
    world.add_stats("cmp", U1, {"minecraft:custom": {"minecraft:jump": 1}})
    world.add_stats("cmp", U2, {"minecraft:custom": {"minecraft:jump": 1}})
    world.set_scoreboard("cmp", ["digs", "kills"],
                         [("Alice", "digs", 10), ("Bob", "digs", 7), ("Npc", "digs", 3),
                          ("Alice", "kills", 2)], {})
    run_snapshot(1000)


def test_reset_entry_recorded_as_zero(world, run_snapshot):
    setup(world, run_snapshot)
    # stattransfer Bob -> Alice: Alice += Bob, Bob and Npc reset; Alice's kills untouched
    world.set_scoreboard("cmp", ["digs", "kills"],
                         [("Alice", "digs", 17), ("Alice", "kills", 2)], {})
    run_snapshot(2000)
    cur = current(world)
    assert cur[(U1, "digs")] == 17 and cur[(U2, "digs")] == 0
    assert cur[("unresolved:Npc", "digs")] == 0 and cur[(U1, "kills")] == 2
    assert sorted(rows(world, "SELECT participant_kind, uuid, raw_name, objective, value "
                              "FROM scoreboard_snapshots WHERE at=2000")) == [
        ("player", U1, "Alice", "digs", 17),
        ("player", U2, "Bob", "digs", 0),
        ("unresolved", None, "Npc", "digs", 0)]
    run_snapshot(3000)  # still gone: no new rows (change-only)
    assert rows(world, "SELECT COUNT(*) FROM scoreboard_snapshots WHERE at=3000") == [(0,)]
    # back on the board: a normal change
    world.set_scoreboard("cmp", ["digs", "kills"],
                         [("Alice", "digs", 17), ("Bob", "digs", 1), ("Alice", "kills", 2)], {})
    run_snapshot(4000)
    assert current(world)[(U2, "digs")] == 1


def _write_without_player_scores(path, objectives):
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="data")
    objs = nbt.TAG_List(name="Objectives", type=nbt.TAG_Compound)
    for o in objectives:
        c = nbt.TAG_Compound()
        c.tags.append(nbt.TAG_String(name="Name", value=o))
        objs.tags.append(c)
    data.tags.append(objs)  # Paper omits an empty PlayerScores tag entirely
    root.tags.append(data)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    root.write_file(str(path))


def test_missing_player_scores_tag_changes_nothing(world, run_snapshot):
    setup(world, run_snapshot)
    before = current(world)
    _write_without_player_scores(
        world.world_dir("cmp") / "data" / "minecraft" / "scoreboard.dat", ["digs", "kills"])
    run_snapshot(2000)
    assert current(world) == before
    assert rows(world, "SELECT COUNT(*) FROM scoreboard_snapshots WHERE at=2000") == [(0,)]


def test_removed_objective_left_to_meta_removed(world, run_snapshot):
    setup(world, run_snapshot)
    world.set_scoreboard("cmp", ["digs"],
                         [("Alice", "digs", 10), ("Bob", "digs", 7), ("Npc", "digs", 3)], {})
    run_snapshot(2000)
    assert current(world)[(U1, "kills")] == 2  # objective gone: not zeroed
    assert rows(world, "SELECT removed FROM scoreboard_meta WHERE server_key='cmp' "
                       "AND objective='kills'") == [(1,)]
    assert rows(world, "SELECT COUNT(*) FROM scoreboard_snapshots WHERE at=2000") == [(0,)]
