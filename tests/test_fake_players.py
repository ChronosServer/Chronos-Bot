"""Carpet fake players (offline-mode, version-3 uuids) are bots on every server."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app
from api.scope import is_fake_uuid

FAKE = "aaaaaaaa-aaaa-3aaa-8aaa-aaaaaaaaaaaa"
REAL = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LB = "/api/stats/leaderboard?kind=stat&category=minecraft:mined&key=minecraft:stone"


def stone(n):
    return {"minecraft:mined": {"minecraft:stone": n}}


def q(world, sql, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("cmp", {FAKE: "Fakey", REAL: "Realo"})
    world.add_stats("cmp", FAKE, stone(50))
    world.add_stats("cmp", REAL, stone(10))
    run_snapshot(1_000_000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


def test_is_fake_uuid():
    assert is_fake_uuid(FAKE)
    assert not is_fake_uuid(REAL)
    assert not is_fake_uuid("u1")
    assert not is_fake_uuid(None)


def test_cmp_leaderboard_hides_fake_unless_include_bots(env):
    rows = env.get(LB + "&scope=cmp").json()["rows"]
    assert [r["uuid"] for r in rows] == [REAL]
    rows = env.get(LB + "&scope=cmp&include_bots=true").json()["rows"]
    assert [r["uuid"] for r in rows] == [FAKE, REAL]


def test_cmp_total_no_bots_excludes_fake(env):
    rows = q(env.world, "SELECT total, total_no_bots FROM stat_totals_current "
             "WHERE server_key='cmp' AND stat_key='minecraft:stone'")
    assert rows == [(60, 10)]
