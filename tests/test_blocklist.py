"""Slur filter on player names: a hard block, even with include_bots."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache, names
from api.main import create_app

BAD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OK = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
A = "/api/stats"
LB = f"{A}/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone&scope=cmp"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


def q(world, sql, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def write_terms(*terms):
    names.BLOCKLIST_PATH.write_text("# comment\n\n" + "\n".join(terms) + "\n", encoding="utf-8")


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    write_terms("badword")
    world.set_usercache("cmp", {BAD: "xX_b4dw0rd_Xx", OK: "Goodie"})
    world.add_stats("cmp", BAD, stone(50))
    world.add_stats("cmp", OK, stone(10))
    run_snapshot(1_000_000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world, c.run = world, run_snapshot
        yield c
    cache.clear()


def test_normalize():
    assert names.normalize("N_1gg3r") == "niger"
    assert names.normalize("Fa$$ot") == "fasot"
    assert names.normalize("aaBB__cc") == "abc"
    assert names.normalize("@5 7") == "as t"


def test_load_terms_and_match():
    write_terms("BadWord", "badword", "  ")
    terms = names.load_terms()
    assert terms == ["badword"]
    assert names.match("xX_b4dw0rd_Xx", terms) == "badword"
    assert names.match("Goodie", terms) is None
    assert names.match(None, terms) is None


def test_missing_files_are_empty():
    assert names.load_terms() == [] and names.load_allow() == set()


def test_blocked_table(env):
    assert q(env.world, "SELECT uuid, name, term FROM blocked_players") == [
        (BAD, "xX_b4dw0rd_Xx", "badword")]


def test_blocked_hidden_everywhere(env):
    for bots in ("false", "true"):
        rows = env.get(LB + f"&include_bots={bots}").json()["rows"]
        assert [r["uuid"] for r in rows] == [OK]
        assert env.get(f"{A}/search", params={"q": "x", "include_bots": bots}).json()[
            "players"] == []
        firsts = env.get(f"{A}/firsts?scope=cmp&include_bots={bots}").json()["rows"]
        assert BAD not in {r["uuid"] for r in firsts}
    assert env.get(f"{A}/player/xX_b4dw0rd_Xx").status_code == 404
    assert env.get(f"{A}/player/{BAD}").status_code == 404
    r = env.get(f"{A}/compare?uuids={BAD},{OK}&keys=minecraft:mined|minecraft:stone")
    assert r.status_code == 404
    # whole-server totals keep their numbers
    assert q(env.world, "SELECT total, total_no_bots FROM stat_totals_current "
             "WHERE server_key='cmp' AND stat_key='minecraft:stone'") == [(60, 60)]


def test_allowlist_by_name_or_uuid(env):
    names.ALLOWLIST_PATH.write_text("XX_B4DW0RD_XX\n", encoding="utf-8")
    env.run(1_000_600)
    assert q(env.world, "SELECT COUNT(*) FROM blocked_players") == [(0,)]
    names.ALLOWLIST_PATH.write_text(BAD + "\n", encoding="utf-8")
    env.run(1_001_200)
    assert q(env.world, "SELECT COUNT(*) FROM blocked_players") == [(0,)]


def test_list_edit_applies_next_run(env):
    write_terms("goodie")
    env.run(1_000_600)
    assert q(env.world, "SELECT uuid FROM blocked_players") == [(OK,)]
    cache.clear()
    rows = env.get(LB).json()["rows"]
    assert [r["uuid"] for r in rows] == [BAD]


def test_check_blocklist_tool(env, capsys):
    from tools import check_blocklist
    assert check_blocklist.main(["--db", env.world.db_path]) == 0
    out = capsys.readouterr().out
    assert "blocked (1):" in out and "xX_b4dw0rd_Xx" in out and BAD in out
    names.ALLOWLIST_PATH.write_text("xx_b4dw0rd_xx\n", encoding="utf-8")
    check_blocklist.main(["--db", env.world.db_path])
    assert "allowlisted (1):" in capsys.readouterr().out
