"""Task 5: firsts, milestones, badges, rivalries, spotlight, name resolver."""
import json
import sqlite3
import time

import derive
import pytest
from mojang import MojangClient

P = "aaaaaaaa-0000-0000-0000-000000000001"
P2 = "aaaaaaaa-0000-0000-0000-000000000002"
BOT = "bbbbbbbb-0000-0000-0000-000000000003"
DAY = 86400


@pytest.fixture
def db(world):
    c = sqlite3.connect(world.db_path)
    yield c
    c.close()


def q(db, sql, *a):
    return db.execute(sql, a).fetchall()


def mined(**kv):
    return {"minecraft:mined": {f"minecraft:{k}": v for k, v in kv.items()}}


def firsts(db, ek=None):
    sql = "SELECT server_key, uuid, category, stat_key, earliest_known, at FROM firsts"
    if ek is not None:
        sql += f" WHERE earliest_known={ek}"
    return q(db, sql + " ORDER BY server_key, category, stat_key")


def badges(db, uuid=None):
    sql = "SELECT uuid, badge_key, server_key FROM badges"
    if uuid:
        return {(b, s) for u, b, s in q(db, sql + " WHERE uuid=?", uuid)}
    return {tuple(r) for r in q(db, sql)}


# ---------------------------------------------------------------- direct-DB helpers

def fresh(run_snapshot, db):
    """Schema + server rows only; marks achievements as seeded so direct
    tests exercise the post-seed paths."""
    run_snapshot(50)
    for t in ("firsts", "milestones", "badges", "rivalries", "spotlight_candidates",
              "activity", "stat_current", "stat_snapshots", "snapshot_runs", "players"):
        db.execute(f"DELETE FROM {t}")
    db.commit()
    return db


def add_run(db, server, at):
    db.execute("INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, rows_written) "
               "VALUES (?, ?, 'stats', 1, 1, 1)", (at, server))


def put(db, server, uuid, cat, key, value, at):
    db.execute("INSERT OR IGNORE INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, NULL, 0, ?, ?)", (uuid, at, at))
    db.execute("INSERT INTO stat_snapshots (at, server_key, uuid, category, stat_key, value) "
               "VALUES (?, ?, ?, ?, ?, ?)", (at, server, uuid, cat, key, value))
    db.execute("INSERT INTO stat_current (server_key, uuid, category, stat_key, value, updated_at, seen_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(server_key, uuid, category, stat_key) "
               "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
               (server, uuid, cat, key, value, at, at))


def active(db, server, uuid, at):
    db.execute("INSERT OR IGNORE INTO activity (server_key, at, uuid) VALUES (?, ?, ?)",
               (server, at, uuid))


def uid(i):
    return f"cccccccc-0000-0000-0000-{i:012d}"


# ---------------------------------------------------------------- firsts

def test_firsts_seeded_once_with_earliest_known(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=5))
    world.add_stats("cmp", P2, mined(stone=9))
    run_snapshot(1000)
    assert firsts(db) == [("cmp", P2, "minecraft:mined", "minecraft:stone", 1, 1000)]
    assert q(db, "SELECT value FROM meta WHERE key='seeded_achievements'") == [("1",)]
    world.add_stats("cmp", P, mined(stone=50))
    run_snapshot(1900)
    assert firsts(db) == [("cmp", P2, "minecraft:mined", "minecraft:stone", 1, 1000)]


def test_new_stat_after_seed_is_real_first(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=5))
    world.add_stats("cmp", P2, mined(stone=9))
    run_snapshot(1000)
    run_snapshot(1900)
    world.add_stats("cmp", P, mined(stone=5, dirt=3))
    world.add_stats("cmp", P2, mined(stone=9, dirt=7))
    run_snapshot(2800)
    assert firsts(db, ek=0) == [("cmp", P2, "minecraft:mined", "minecraft:dirt", 0, 2800)]


def test_firsts_exclude_smp_bot(world, run_snapshot, db):
    world.set_usercache("smp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    world.add_stats("smp", P, mined(stone=5))
    world.add_stats("smp", BOT, mined(stone=500))
    run_snapshot(1000)
    assert [r[1] for r in firsts(db)] == [P]
    world.add_stats("smp", P, mined(stone=5, dirt=1))
    world.add_stats("smp", BOT, mined(stone=500, dirt=99))
    run_snapshot(1900)
    assert [(r[1], r[3], r[4]) for r in firsts(db, ek=0)] == [(P, "minecraft:dirt", 0)]


def test_scoreboard_first_seeded_and_real(world, run_snapshot, db):
    world.set_usercache("cmp", {P: "Alice", P2: "Bob"})
    world.add_stats("cmp", P, mined(stone=1))
    world.add_stats("cmp", P2, mined(stone=1))
    world.set_scoreboard("cmp", ["kills"], [("Alice", "kills", 3), ("Ghost", "kills", 99)], {})
    run_snapshot(1000)
    assert ("cmp", P, "scoreboard", "kills", 1, 1000) in firsts(db)
    world.set_scoreboard("cmp", ["kills", "digs"],
                         [("Alice", "kills", 3), ("Bob", "digs", 4), ("Ghost", "digs", 99)], {})
    run_snapshot(1900)
    assert firsts(db, ek=0) == [("cmp", P2, "scoreboard", "digs", 0, 1900)]


def test_new_server_first_run_is_seeded_not_real(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=5))
    run_snapshot(1000)
    world.add_stats("cmp2", P, mined(dirt=5))  # cmp2 sees data for the first time
    run_snapshot(1900)
    assert [(r[0], r[4]) for r in firsts(db) if r[0] == "cmp2"] == [("cmp2", 1)]


# ---------------------------------------------------------------- milestones

def ms(db, ek=None):
    sql = "SELECT uuid, stat_key, threshold, earliest_known, at, value_at FROM milestones"
    if ek is not None:
        sql += f" WHERE earliest_known={ek}"
    return q(db, sql + " ORDER BY stat_key, threshold")


def test_milestone_rules_seeded(world, run_snapshot, db):
    run_snapshot(1000)
    rules = {r[0]: (r[1], r[2], json.loads(r[3])) for r in q(
        db, "SELECT rule_key, category, stat_key, thresholds FROM milestone_rules")}
    for c in ("mined", "used", "crafted", "broken", "picked_up", "dropped", "killed", "killed_by"):
        assert rules[f"cat:minecraft:{c}"] == (
            f"minecraft:{c}", None, [1000, 10000, 100000, 1000000, 10000000])
    by_key = {v[1]: v[2] for v in rules.values() if v[1]}
    assert by_key["minecraft:play_time"] == [h * 72000 for h in (100, 500, 1000, 5000, 10000)]
    assert by_key["minecraft:walk_one_cm"] == [k * 100000 for k in (100, 1000, 10000)]
    assert by_key["minecraft:deaths"] == [100, 1000, 10000]
    assert len(rules) == 11
    assert not [v for v in rules.values() if v[0] == "scoreboard"]


def test_milestone_crossing(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=900))
    run_snapshot(1000)
    assert ms(db) == []
    world.add_stats("cmp", P, mined(stone=1500))
    run_snapshot(1900)
    assert ms(db) == [(P, "minecraft:stone", 1000, 0, 1900, 1500)]
    world.add_stats("cmp", P, mined(stone=1600))
    run_snapshot(2800)
    assert len(ms(db)) == 1


def test_milestone_seed_marks_existing(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=25000))
    run_snapshot(1000)
    assert [(r[2], r[3]) for r in ms(db)] == [(1000, 1), (10000, 1)]


def test_milestone_per_key_rule_and_bot_excluded(world, run_snapshot, db):
    world.set_usercache("smp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    world.add_stats("smp", P, {"minecraft:custom": {"minecraft:deaths": 99}})
    world.add_stats("smp", BOT, {"minecraft:custom": {"minecraft:deaths": 99}})
    run_snapshot(1000)
    world.add_stats("smp", P, {"minecraft:custom": {"minecraft:deaths": 101}})
    world.add_stats("smp", BOT, {"minecraft:custom": {"minecraft:deaths": 101}})
    run_snapshot(1900)
    assert ms(db) == [(P, "minecraft:deaths", 100, 0, 1900, 101)]


def test_derive_twice_same_run_no_duplicates(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=25000))
    world.add_stats("cmp", P2, mined(stone=900))
    run_snapshot(1000)
    world.add_stats("cmp", P2, mined(stone=1500, dirt=4))
    run_snapshot(1900)

    def counts():
        return [q(db, f"SELECT COUNT(*) FROM {t}")[0][0]
                for t in ("milestones", "firsts", "badges")]
    before = counts()
    assert before[0] == 3 and before[1] == 2
    derive.run_derive(db, 1900)
    derive.run_derive(db, 1900)
    assert counts() == before
    # same-run re-derive does not turn the run-1 seed into real achievements
    derive.run_derive(db, 1000)
    assert counts() == before


# ---------------------------------------------------------------- badges

def test_badges_firsts_ignore_earliest_known(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=5))
    run_snapshot(1000)
    assert ("firsts:1", "cmp") not in badges(db, P)
    assert ("top1:1", "cmp") in badges(db, P)
    world.add_stats("cmp", P, mined(stone=5, dirt=1))
    run_snapshot(1900)
    assert ("firsts:1", "cmp") in badges(db, P)
    assert ("firsts:10", "cmp") not in badges(db, P)


def test_badge_category_tier_sums_keys(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=600000, dirt=500000))
    run_snapshot(1000)
    b = {k for k, _ in badges(db, P)}
    assert {"cat:minecraft:mined:1", "cat:minecraft:mined:2"} <= b
    assert "cat:minecraft:mined:3" not in b
    assert "milestone:1000000" not in b  # no single key reached 1M


def test_badge_big_numbers_and_veteran(world, run_snapshot, db):
    world.add_stats("cmp", P, {"minecraft:mined": {"minecraft:stone": 1000000},
                               "minecraft:custom": {"minecraft:play_time": 72000000}})
    world.add_stats("cmp", P2, {"minecraft:custom": {"minecraft:play_time": 71999999}})
    run_snapshot(1000)
    b = {k for k, _ in badges(db, P)}
    assert {"milestone:1000000", "veteran", "cat:minecraft:mined:2"} <= b
    assert "milestone:10000000" not in b
    assert "veteran" not in {k for k, _ in badges(db, P2)}


def test_big_numbers_only_from_count_rules(world, run_snapshot, db):
    world.add_stats("cmp", P, {"minecraft:custom": {"minecraft:walk_one_cm": 10000000}})
    run_snapshot(1000)
    assert q(db, "SELECT threshold FROM milestones WHERE uuid=?", P) == [(10000000,)]
    assert "milestone:10000000" not in {k for k, _ in badges(db, P)}


def test_badge_top1_counts_simultaneous_keys(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", 1000)
    for i in range(10):
        put(db, "cmp", P, "minecraft:mined", f"k{i}", 10, 1000)
        put(db, "cmp", P2, "minecraft:mined", f"k{i}", 5, 1000)
    db.commit()
    derive.derive_badges(db, 1000)
    b = {k for k, _ in badges(db, P)}
    assert {"top1:1", "top1:10"} <= b and "top1:50" not in b
    assert badges(db, P2) == set()


def test_badge_kept_after_losing_top1(world, run_snapshot, db):
    world.add_stats("cmp", P, mined(stone=10))
    run_snapshot(1000)
    assert ("top1:1", "cmp") in badges(db, P)
    world.add_stats("cmp", P2, mined(stone=20))
    run_snapshot(1900)
    assert ("top1:1", "cmp") in badges(db, P)
    assert ("top1:1", "cmp") in badges(db, P2)


def test_badges_exclude_smp_bot(world, run_snapshot, db):
    world.set_usercache("smp", {P: "Alice", BOT: "BotGuy"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    world.add_stats("smp", P, mined(stone=1))
    world.add_stats("smp", BOT, mined(stone=2000000))
    run_snapshot(1000)
    assert badges(db, BOT) == set()
    assert ("top1:1", "smp") in badges(db, P)


def local(y, mo, d, h):
    return int(time.mktime((y, mo, d, h, 0, 0, 0, 0, -1)))


def test_badge_night_owl(run_snapshot, db):
    fresh(run_snapshot, db)
    run_at = local(2026, 11, 24, 12)
    for d in range(2, 23):  # 21 buckets at 02:00
        active(db, "cmp", P, local(2026, 11, d, 2))
        active(db, "cmp", P2, local(2026, 11, d, 2))
    for d in range(2, 21):  # 19 buckets at 14:00 -> P has 40 total
        active(db, "cmp", P, local(2026, 11, d, 14))
    for d in range(2, 20):  # 18 -> P2 has 39 total
        active(db, "cmp", P2, local(2026, 11, d, 14))
    db.commit()
    derive.derive_badges(db, run_at)
    assert ("night_owl", "cmp") in badges(db, P)
    assert ("night_owl", "cmp") not in badges(db, P2)


def test_badge_night_owl_needs_majority(run_snapshot, db):
    fresh(run_snapshot, db)
    run_at = local(2026, 11, 24, 12)
    for d in range(2, 22):
        active(db, "cmp", P, local(2026, 11, d, 3))
        active(db, "cmp", P, local(2026, 11, d, 15))  # exactly 50% at night
    db.commit()
    derive.derive_badges(db, run_at)
    assert ("night_owl", "cmp") not in badges(db, P)


def test_badge_regular(run_snapshot, db):
    fresh(run_snapshot, db)
    run_at = local(2026, 11, 24, 23)
    for d in range(4, 24):  # 20 distinct days
        active(db, "cmp", P, local(2026, 11, d, 12))
    for d in range(5, 24):  # 19 distinct days (two buckets on each)
        active(db, "cmp", P2, local(2026, 11, d, 10))
        active(db, "cmp", P2, local(2026, 11, d, 11))
    active(db, "cmp", P2, local(2026, 10, 1, 12))  # older than 30 days
    db.commit()
    derive.derive_badges(db, run_at)
    assert ("regular", "cmp") in badges(db, P)
    assert ("regular", "cmp") not in badges(db, P2)


# ---------------------------------------------------------------- rivalries

RUN = 100 * DAY
T0 = RUN - 10 * DAY
STONE = ("minecraft:mined", "minecraft:stone")


def race(db, key, a, b, a_then, b_then, a_now, b_now, cat="minecraft:mined"):
    put(db, "cmp", a, cat, key, a_then, T0)
    put(db, "cmp", b, cat, key, b_then, T0)
    if a_now != a_then:
        put(db, "cmp", a, cat, key, a_now, RUN - DAY)
    if b_now != b_then:
        put(db, "cmp", b, cat, key, b_now, RUN - DAY)
    for u in (a, b):
        active(db, "cmp", u, RUN - DAY)


def rivals(db):
    return q(db, "SELECT category, stat_key, uuid_a, uuid_b, gap, gap_7d_ago, computed_at "
                 "FROM rivalries ORDER BY stat_key, uuid_a")


def test_rivalry_detected_and_gap_7d_ago(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    race(db, "minecraft:stone", P, P2, 10000, 9000, 10100, 9950)
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert rivals(db) == [(*STONE, P, P2, 150, 1000, RUN)]


def test_rivalry_filters(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    race(db, "small", P, P2, 900, 800, 990, 985)          # leader < 1000
    race(db, "wide", P, P2, 10000, 9000, 10000, 9799)     # gap 201 > 2% of 10000
    race(db, "edge", P, P2, 10000, 9000, 10000, 9800)     # gap 200 == 2%: kept
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert [r[1] for r in rivals(db)] == ["edge"]


def test_rivalry_requires_both_active(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    race(db, "k", P, P2, 10000, 9000, 10100, 9950)
    db.execute("DELETE FROM activity WHERE uuid=?", (P,))
    active(db, "cmp", P, RUN - 8 * DAY)  # outside the 7-day window
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert rivals(db) == []


def test_rivalry_static_pair_ignored(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    race(db, "k", P, P2, 5000, 4999, 5000, 4999)
    # both gained equally: gap unchanged
    race(db, "k2", P, P2, 5000, 4999, 5100, 5099)
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert rivals(db) == []


def test_rivalry_b_must_have_gained(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    race(db, "k", P, P2, 5000, 4950, 5010, 4950)  # gap changed but B idle
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert rivals(db) == []


def test_rivalry_gap_7d_ago_clamped_to_tracking_start(run_snapshot, db):
    fresh(run_snapshot, db)
    start = RUN - 2 * DAY
    add_run(db, "cmp", start)
    put(db, "cmp", P, *STONE, 10000, start)
    put(db, "cmp", P2, *STONE, 9000, start)
    put(db, "cmp", P2, *STONE, 9950, RUN)
    put(db, "cmp", P, *STONE, 10100, RUN)
    active(db, "cmp", P, RUN)
    active(db, "cmp", P2, RUN)
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert [(r[4], r[5]) for r in rivals(db)] == [(150, 1000)]


def test_rivalry_caps_per_player_and_key(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    x = uid(999)
    for i in range(7):  # x is B in 7 keys against 7 different leaders
        race(db, f"p{i}", uid(i), x, 10000, 9000, 10100, 9950 + i)
    zs = [uid(100 + i) for i in range(5)]  # one key, 4 adjacent moving pairs
    for i, z in enumerate(zs):
        put(db, "cmp", z, "minecraft:mined", "kk", 20000 - 100 * i, T0)
        put(db, "cmp", z, "minecraft:mined", "kk", 20000 - 100 * i + 50 * i, RUN - DAY)
        active(db, "cmp", z, RUN - DAY)
    db.commit()
    derive.derive_rivalries(db, RUN)
    rows = rivals(db)
    assert sum(1 for r in rows if x in (r[2], r[3])) == 5
    # lowest overtake_days first: x gained most in p6..p2
    assert sorted(r[1] for r in rows if r[3] == x) == ["p2", "p3", "p4", "p5", "p6"]
    assert sum(1 for r in rows if r[1] == "kk") == 3


def test_overtaker_awarded_on_flip(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    put(db, "cmp", P, *STONE, 10000, T0)
    put(db, "cmp", P2, *STONE, 10050, RUN)
    put(db, "cmp", uid(1), *STONE, 3000, T0)
    put(db, "cmp", uid(2), *STONE, 3000, T0)
    db.execute("INSERT INTO rivalries VALUES ('cmp', ?, ?, ?, ?, 100, 300, ?)",
               (*STONE, P, P2, RUN - 900))
    # a stored tie that is still a tie is not an overtake
    db.execute("INSERT INTO rivalries VALUES ('cmp', ?, ?, ?, ?, 0, 5, ?)",
               (*STONE, uid(1), uid(2), RUN - 900))
    db.commit()
    derive.derive_rivalries(db, RUN)
    assert badges(db) == {(P2, "overtaker", "cmp")}


# ---------------------------------------------------------------- spotlight

def spot(db, trigger):
    return q(db, "SELECT server_key, uuid, category, stat_key, value, detail_json FROM "
                 "spotlight_candidates WHERE trigger=? ORDER BY stat_key, uuid", trigger)


def test_spotlight_outlier_needs_10_holders(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    for i in range(9):
        put(db, "cmp", uid(i), "minecraft:mined", "ten", 10, T0)
    put(db, "cmp", P, "minecraft:mined", "ten", 100, T0)   # 10 holders, median 10
    for i in range(8):
        put(db, "cmp", uid(i), "minecraft:mined", "nine", 10, T0)
    put(db, "cmp", P, "minecraft:mined", "nine", 1000, T0)  # only 9 holders
    put(db, "cmp", uid(50), "minecraft:mined", "ten", 0, T0)  # zero holder ignored
    # 'two': 10 holders, median 10; two holders >= 100 -> only the top one
    for i in range(8):
        put(db, "cmp", uid(i), "minecraft:mined", "two", 10, T0)
    put(db, "cmp", P, "minecraft:mined", "two", 500, T0)
    put(db, "cmp", P2, "minecraft:mined", "two", 200, T0)
    db.commit()
    derive.derive_spotlight(db, RUN)
    rows = spot(db, "outlier")
    assert [(r[1], r[3], r[4]) for r in rows] == [(P, "ten", 100), (P, "two", 500)]
    d = json.loads(rows[0][5])
    assert d["median"] == 10 and d["holders"] == 10


def test_spotlight_rare_max_3_holders(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    for i in range(3):
        put(db, "cmp", uid(i), "minecraft:killed", "guardian", i + 1, T0)
    for i in range(4):
        put(db, "cmp", uid(i), "minecraft:killed", "ghast", i + 1, T0)
    db.commit()
    derive.derive_spotlight(db, RUN)
    rows = spot(db, "rare")
    assert [(r[1], r[3], r[4]) for r in rows] == [(uid(2), "guardian", 3)]
    assert json.loads(rows[0][5])["holders"] == 3


def test_spotlight_mover_threshold(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    put(db, "cmp", P, "minecraft:mined", "a", 2000, T0)
    put(db, "cmp", P, "minecraft:mined", "a", 3000, RUN - DAY)    # +1000 = 50%: yes
    put(db, "cmp", P2, "minecraft:mined", "a", 4000, T0)
    put(db, "cmp", P2, "minecraft:mined", "a", 5999, RUN - DAY)   # +1999 < 50%: no
    put(db, "cmp", uid(1), "minecraft:mined", "a", 100, T0)
    put(db, "cmp", uid(1), "minecraft:mined", "a", 1099, RUN - DAY)  # +999 < 1000: no
    put(db, "cmp", uid(2), "minecraft:mined", "a", 1500, RUN - DAY)  # new, +1500: yes
    db.commit()
    derive.derive_spotlight(db, RUN)
    rows = spot(db, "mover")
    assert {(r[1], r[4]) for r in rows} == {(P, 3000), (uid(2), 1500)}
    assert json.loads([r for r in rows if r[1] == P][0][5])["delta"] == 1000


def test_spotlight_mover_top_20(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    for i in range(25):
        put(db, "cmp", uid(i), "minecraft:mined", "a", 2000 + i, RUN - DAY)
    db.commit()
    derive.derive_spotlight(db, RUN)
    rows = spot(db, "mover")
    assert len(rows) == 20 and min(r[4] for r in rows) == 2005


def test_spotlight_recent_milestone_and_first(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    put(db, "cmp", P, *STONE, 1500, T0)
    ins = ("INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key, threshold, "
           "value_at, earliest_known) VALUES (?, 'cmp', ?, 'r', 'c', ?, 1000, 1500, ?)")
    db.execute(ins, (RUN - 6 * DAY, P, "new", 0))
    db.execute(ins, (RUN - 8 * DAY, P, "old", 0))
    db.execute(ins, (RUN - DAY, P, "seeded", 1))
    insf = ("INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known) "
            "VALUES (?, 'cmp', ?, ?, ?, ?)")
    db.execute(insf, (RUN - 29 * DAY, P, *STONE, 0))
    db.execute(insf, (RUN - 31 * DAY, P, "minecraft:mined", "old", 0))
    db.execute(insf, (RUN - DAY, P, "minecraft:mined", "seeded", 1))
    db.commit()
    derive.derive_spotlight(db, RUN)
    assert [r[3] for r in spot(db, "recent_milestone")] == ["new"]
    assert [(r[3], r[4]) for r in spot(db, "recent_first")] == [("minecraft:stone", 1500)]


def test_spotlight_recent_items_exclude_approximate(run_snapshot, db):
    """Imported (approximate=1) crossings and firsts are dated at a backup
    point, not when they happened: never "recent"."""
    fresh(run_snapshot, db)
    add_run(db, "cmp", T0)
    put(db, "cmp", P, *STONE, 1500, T0)
    put(db, "cmp", P, "minecraft:mined", "dirt", 7, T0)
    ins = ("INSERT INTO milestones (at, server_key, uuid, rule_key, category, stat_key, threshold, "
           "value_at, earliest_known, approximate) VALUES (?, 'cmp', ?, 'r', 'c', ?, 1000, 1500, 0, ?)")
    db.execute(ins, (RUN - DAY, P, "real", 0))
    db.execute(ins, (RUN - DAY, P, "approx", 1))
    insf = ("INSERT INTO firsts (at, server_key, uuid, category, stat_key, earliest_known, "
            "approximate) VALUES (?, 'cmp', ?, ?, ?, 0, ?)")
    db.execute(insf, (RUN - DAY, P, *STONE, 0))
    db.execute(insf, (RUN - DAY, P, "minecraft:mined", "minecraft:dirt", 1))
    db.commit()
    derive.derive_spotlight(db, RUN)
    rm = spot(db, "recent_milestone")
    rf = spot(db, "recent_first")
    assert [r[3] for r in rm] == ["real"]
    assert [r[3] for r in rf] == ["minecraft:stone"]
    assert json.loads(rm[0][5])["approximate"] == 0
    assert json.loads(rf[0][5])["approximate"] == 0


def test_spotlight_excludes_smp_bot(run_snapshot, db):
    fresh(run_snapshot, db)
    add_run(db, "smp", T0)
    put(db, "smp", BOT, "minecraft:killed", "guardian", 5, T0)
    db.commit()
    derive.derive_spotlight(db, RUN)
    assert spot(db, "rare") == []


# ---------------------------------------------------------------- names

def test_resolve_names_uses_budget(run_snapshot, db):
    fresh(run_snapshot, db)
    for i, u in enumerate((P, P2, BOT)):
        db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
                   "VALUES (?, NULL, 0, 1, ?)", (u, 100 + i))
    db.commit()
    calls = []

    def fetch(method, url, body):
        calls.append(url)
        return 200, json.dumps({"name": "N" + url[-1]}).encode()
    client = MojangClient(fetch, db, 5000, budget=3)
    client.calls = 1  # 2 calls left
    derive.resolve_names(db, 5000, client)
    assert len(calls) == 2
    got = dict(q(db, "SELECT uuid, name_resolved FROM players"))
    assert got == {BOT: 1, P2: 1, P: 0}  # most recently observed first
    assert q(db, "SELECT name FROM players WHERE uuid=?", BOT) == [("N3",)]
    derive.resolve_names(db, 5000, None)  # no client: no-op


def test_resolve_names_not_found_leaves_unresolved(run_snapshot, db):
    fresh(run_snapshot, db)
    db.execute("INSERT INTO players (uuid, name, name_resolved, first_seen, last_observed) "
               "VALUES (?, 'old', 0, 1, 1)", (P,))
    db.commit()
    client = MojangClient(lambda m, u, b: (404, b""), db, 5000, budget=5)
    derive.resolve_names(db, 5000, client)
    assert q(db, "SELECT name, name_resolved FROM players") == [("old", 0)]


# ---------------------------------------------------------------- query plans

def test_run_row_queries_use_at_indexes(world, run_snapshot, db):
    """Every "rows written this run" read must seek (server_key, at), not
    scan all of a server's history (R18)."""
    import re
    world.set_usercache("cmp", {P: "Alice"})
    world.add_stats("cmp", P, mined(stone=5))
    world.set_scoreboard("cmp", ["kills"], [("Alice", "kills", 3)], {})
    run_snapshot(1000)
    stmts = []
    db.set_trace_callback(stmts.append)
    derive.run_derive(db, 1900)
    db.set_trace_callback(None)
    run_reads = [s for s in stmts if re.search(r"\bat\s*=\s*1900\b", s)
                 and re.search(r"\bFROM (stat|scoreboard)_snapshots\b", s)]
    tables = []
    for s in run_reads:
        plan = " | ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN " + s))
        m = re.search(r"USING (?:COVERING )?INDEX (idx_(?:stat|scoreboard)_at) \(server_key=\? AND at=\?\)",
                      plan)
        assert m, (s, plan)
        tables.append(m.group(1))
    # activity + firsts (stats, scoreboard) + milestones (stats, scoreboard)
    assert tables.count("idx_stat_at") >= 3 and tables.count("idx_scoreboard_at") >= 2
