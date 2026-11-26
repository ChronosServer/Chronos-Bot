"""Servers and scopes come back in stats_tracking.servers' config order."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app
from api.scope import list_scopes, order_servers, resolve_scope, scope_group_servers

U1 = "11111111-1111-1111-1111-111111111111"
STONE = {"minecraft:mined": {"minecraft:stone": 5}}


def reorder(world, *keys):
    """Rewrite config's servers map in this order (keys left out are removed)."""
    servers = world.config["stats_tracking"]["servers"]
    world.config["stats_tracking"]["servers"] = {k: servers[k] for k in keys}


def db_conn(world):
    return sqlite3.connect(world.db_path)


@pytest.fixture
def client(world, run_snapshot, monkeypatch):
    seed(world, run_snapshot, 1000)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def seed(world, run_snapshot, at):
    world.set_usercache("smp", {U1: "Alice"})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    for key in ("smp", "cmp", "cmp2"):
        world.add_stats(key, U1, STONE)
        world.set_scoreboard(key, ["kills"], [("Alice", "kills", 1)],
                             {"t": ["Alice"]} if key == "smp" else {})
    run_snapshot(at)


def test_positions_written_from_config(world, run_snapshot):
    seed(world, run_snapshot, 1000)
    c = db_conn(world)
    try:
        rows = c.execute("SELECT key, position FROM servers ORDER BY key").fetchall()
        assert dict(rows) == {"all": None, "smp": 0, "cmp": 1, "cmp2": 2}
        assert resolve_scope(c, "all").servers == ("smp", "cmp", "cmp2")
        assert list_scopes(c) == ["all", "smp", "cmps", "cmp", "cmp2"]
    finally:
        c.close()


def test_reordered_config_reorders_everything(world, run_snapshot, client):
    m1 = client.get("/api/stats/meta")
    assert [s["key"] for s in m1.json()["servers"]] == ["smp", "cmp", "cmp2"]
    assert m1.json()["scopes"] == ["all", "smp", "cmps", "cmp", "cmp2"]

    reorder(world, "cmp2", "cmp", "smp")
    run_snapshot(2000)
    r = client.get("/api/stats/meta", headers={"If-None-Match": m1.headers["ETag"]})
    assert r.status_code == 200 and r.headers["ETag"] != m1.headers["ETag"]
    j = r.json()
    assert [s["key"] for s in j["servers"]] == ["cmp2", "cmp", "smp"]
    assert list(j["tracking_started_at"]) == ["cmp2", "cmp", "smp"]
    # Documented structure kept: all; ungrouped; groups; grouped servers.
    assert j["scopes"] == ["all", "smp", "cmps", "cmp2", "cmp"]
    assert client.get(f"/api/stats/player/{U1}").json()["servers"] == ["cmp2", "cmp", "smp"]
    assert client.get(f"/api/stats/player/{U1}?scope=cmps").json()["servers"] == ["cmp2", "cmp"]
    objs = client.get("/api/stats/search?q=kills").json()["objectives"]
    assert objs == [{"objective": "kills", "servers": ["cmp2", "cmp", "smp"]}]


def test_meta_scope_groups(world, run_snapshot, client):
    j = client.get("/api/stats/meta").json()
    assert j["scope_groups"] == {"cmps": ["cmp", "cmp2"]}
    reorder(world, "smp", "cmp2", "cmp")
    run_snapshot(2000)
    assert client.get("/api/stats/meta").json()["scope_groups"] == {"cmps": ["cmp2", "cmp"]}


def test_scope_group_servers_skips_empty_groups(world, run_snapshot):
    seed(world, run_snapshot, 1000)
    c = db_conn(world)
    try:
        got = scope_group_servers(c, {"cmps": "cmp", "nothing": "nope", "main": "smp"})
        assert got == {"cmps": ["cmp", "cmp2"], "main": ["smp"]}
    finally:
        c.close()


def test_server_dropped_from_config_goes_last_alphabetically(world, run_snapshot):
    seed(world, run_snapshot, 1000)
    reorder(world, "smp")  # cmp, cmp2 stay provisioned in the DB, but not in config
    run_snapshot(2000)
    c = db_conn(world)
    try:
        assert dict(c.execute("SELECT key, position FROM servers WHERE key != 'all'")) == {
            "smp": 0, "cmp": None, "cmp2": None}
        assert resolve_scope(c, "all").servers == ("smp", "cmp", "cmp2")
        assert order_servers(c, ["cmp2", "smp", "cmp", "smp"]) == ["smp", "cmp", "cmp2"]
    finally:
        c.close()


def test_migrate_v3_db_adds_position_then_snapshot_fills_it(world, run_snapshot):
    import snapshot
    from sql.migrations import migrate
    seed(world, run_snapshot, 1000)
    c = db_conn(world)
    try:  # roll back to a v3 shape: no position column
        c.execute("ALTER TABLE servers DROP COLUMN position")
        c.execute("PRAGMA user_version = 3")
        c.commit()
        migrate(c)
        assert c.execute("PRAGMA user_version").fetchone()[0] == 6
        # Before the next snapshot every position is NULL: alphabetical, still valid.
        assert resolve_scope(c, "all").servers == ("cmp", "cmp2", "smp")
        snapshot.sync_servers_table(c, world.config)
        assert resolve_scope(c, "all").servers == ("smp", "cmp", "cmp2")
    finally:
        c.close()


def test_order_servers_unknown_keys_last(world, run_snapshot):
    seed(world, run_snapshot, 1000)
    c = db_conn(world)
    try:
        assert order_servers(c, ["zzz", "cmp2", "aaa", "smp"]) == ["smp", "cmp2", "aaa", "zzz"]
    finally:
        c.close()
