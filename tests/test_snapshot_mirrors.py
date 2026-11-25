"""Live vanilla-mirror detection (M5) and a missing dictionary (M6)."""
import json
import sqlite3

import pytest

import snapshot

U1 = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def dicts(tmp_path, monkeypatch):
    """A dictionaries/ folder of an old and the current version."""
    d = tmp_path / "dictionaries"
    d.mkdir()
    (d / "dictionary-1.16.json").write_text(json.dumps(
        {"m-grass_path": "m-grass_path", "m-stone": "m-stone"}), encoding="utf-8")
    (d / "dictionary-26.2.json").write_text(json.dumps(
        {"m-dirt_path": "m-dirt_path", "m-stone": "m-stone"}), encoding="utf-8")
    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", d)
    return d


def board(world):
    world.add_stats("smp", U1, {})
    world.set_usercache("smp", {U1: "Alice"})
    world.set_scoreboard("smp", ["m-grass_path", "m-dirt_path", "digs"],
                         [("Alice", "m-grass_path", 5), ("Alice", "m-dirt_path", 6),
                          ("Alice", "digs", 7)], {"t": ["Alice"]})


def meta(world):
    c = sqlite3.connect(world.db_path)
    try:
        return dict(c.execute("SELECT objective, is_vanilla_mirror FROM scoreboard_meta "
                              "WHERE server_key='smp'").fetchall())
    finally:
        c.close()


def tracked(world):
    c = sqlite3.connect(world.db_path)
    try:
        return {o for (o,) in c.execute(
            "SELECT DISTINCT objective FROM scoreboard_snapshots WHERE server_key='smp'")}
    finally:
        c.close()


def sb_runs(world):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute("SELECT at, ok, error FROM snapshot_runs WHERE server_key='smp' "
                         "AND kind='scoreboards' ORDER BY id").fetchall()
    finally:
        c.close()


def test_old_version_mirrors_on_live_board_are_flagged(world, run_snapshot, dicts):
    """The live mirror set is the union of every bundled dictionary, as in the
    import: an old-version mirror still on the board (m-grass_path) is a mirror."""
    board(world)
    run_snapshot(1000, dictionary={"m-dirt_path": "m-dirt_path"})
    assert meta(world) == {"m-grass_path": 1, "m-dirt_path": 1, "digs": 0}
    assert tracked(world) == {"digs"}


def test_missing_dictionary_falls_back_to_bundled_union(world, dicts, monkeypatch, capsys):
    def boom(*a, **kw):
        raise OSError("no network")
    monkeypatch.setattr(snapshot, "fetch_dictionary", boom)
    board(world)
    snapshot.run_once(config=world.config, now_fn=lambda: 1000,
                      mojang_fetch=lambda m, u, b: (0, b""))
    assert meta(world) == {"m-grass_path": 1, "m-dirt_path": 1, "digs": 0}
    assert tracked(world) == {"digs"}
    assert sb_runs(world)[0][1] == 1
    out = capsys.readouterr().out
    assert "[warn]" in out and "bundled" in out


def test_no_dictionary_at_all_skips_scoreboard_step(world, run_snapshot, tmp_path,
                                                    monkeypatch, capsys):
    """No current dictionary and no bundled one: mirrors can't be told apart.
    The scoreboard step is skipped (ok=0, clear error); it neither writes
    mirror scores nor resets the stored mirror flags."""
    d = tmp_path / "dictionaries"
    d.mkdir()
    (d / "dictionary-1.16.json").write_text(json.dumps({"m-grass_path": "m-grass_path"}),
                                            encoding="utf-8")
    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", d)
    board(world)
    run_snapshot(1000, dictionary={"m-dirt_path": "m-dirt_path"})
    assert meta(world)["m-grass_path"] == 1

    monkeypatch.setattr(snapshot, "DICTIONARY_DIR", tmp_path / "none")

    def boom(*a, **kw):
        raise OSError("no network")
    monkeypatch.setattr(snapshot, "fetch_dictionary", boom)
    world.set_scoreboard("smp", ["m-grass_path", "m-dirt_path", "digs"],
                         [("Alice", "m-grass_path", 50), ("Alice", "m-dirt_path", 60),
                          ("Alice", "digs", 70)], {"t": ["Alice"]})
    snapshot.run_once(config=world.config, now_fn=lambda: 2000,
                      mojang_fetch=lambda m, u, b: (0, b""))
    runs = sb_runs(world)
    assert runs[0][1] == 1
    assert runs[-1][:2] == (2000, 0) and "dictionary" in runs[-1][2]
    assert meta(world) == {"m-grass_path": 1, "m-dirt_path": 1, "digs": 0}
    assert tracked(world) == {"digs"}
    c = sqlite3.connect(world.db_path)
    assert c.execute("SELECT COUNT(*) FROM scoreboard_snapshots WHERE at=2000").fetchone()[0] == 0
    c.close()
    assert "scoreboards: ERROR" in capsys.readouterr().out


def test_bundled_mirror_names(dicts):
    assert snapshot.bundled_mirror_names() == {"m-grass_path", "m-dirt_path", "m-stone"}


def test_prefixed_objectives_are_mirrors_without_a_dictionary_entry(world, run_snapshot, dicts):
    """every-scoreboard copies no bundled dictionary lists (z-play_one_m+748 =
    the pre-1.17 play_one_minute, u-moss) are mirrors by name pattern
    (settings snapshot.mirror_prefix); real objectives like dig-all are not.
    Already stored rows of a newly flagged objective are hidden, not deleted."""
    world.add_stats("smp", U1, {})
    world.set_usercache("smp", {U1: "Alice"})
    objs = ["z-play_one_m+748", "u-moss", "dig-all", "Deaths"]
    world.set_scoreboard("smp", objs, [("Alice", o, 3) for o in objs], {"t": ["Alice"]})
    tracking = world.config["stats_tracking"]
    tracking.setdefault("snapshot", {})["mirror_prefix"] = ""   # rule off: stored as real
    run_snapshot(1000, dictionary={})
    assert meta(world) == {o: 0 for o in objs}
    tracking["snapshot"]["mirror_prefix"] = "^(m|u|c|b|p|d|k|kb|z)-"
    world.set_scoreboard("smp", objs, [("Alice", o, 4) for o in objs], {"t": ["Alice"]})
    run_snapshot(2000, dictionary={})
    assert meta(world) == {"z-play_one_m+748": 1, "u-moss": 1, "dig-all": 0, "Deaths": 0}
    c = sqlite3.connect(world.db_path)
    try:
        # no new score rows for the mirrors; the real ones change as usual
        assert sorted(c.execute("SELECT objective FROM scoreboard_snapshots WHERE at=2000")) == [
            ("Deaths",), ("dig-all",)]
        # their earlier rows stay but the API hides them (firsts too)
        assert c.execute("SELECT COUNT(*) FROM scoreboard_snapshots WHERE at=1000").fetchone()[0] == 4
    finally:
        c.close()
