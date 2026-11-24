import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
U4 = "44444444-4444-4444-4444-444444444444"
S = "/api/stats/search"


def stats(n):
    return {"minecraft:mined": {"minecraft:stone": n, "minecraft:cobblestone": n,
                                "minecraft:deepslate": n},
            "minecraft:custom": {"minecraft:play_time": n}}


def sql(world, stmt, params=()):
    c = sqlite3.connect(world.db_path)
    try:
        c.execute(stmt, params)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    world.set_usercache("smp", {U1: "Alice", U2: "alfred", U3: "Botty", U4: "Malice"})
    world.set_scoreboard(
        "smp", ["kills", "deaths", "minecraft.mined:minecraft.stone"],
        [("Alice", "kills", 3), ("Alfred", "kills", 1),
         ("Alice", "minecraft.mined:minecraft.stone", 8)],
        {"t": ["Alice", "alfred", "Malice"]})
    for u in (U1, U2, U3, U4):
        world.add_stats("smp", u, stats(5))
    run_snapshot(1_000_000, dictionary={"x": "minecraft.mined:minecraft.stone"})
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.world = world
        yield c
    cache.clear()


def test_search_prefix_before_substring(env):
    names = [p["name"] for p in env.get(S, params={"q": "al"}).json()["players"]]
    assert names == ["Alice", "alfred", "Malice"]  # prefixes (shorter first), then substring
    assert env.get(S, params={"q": "AL", "limit": 1}).json()["players"][0]["name"] == "Alice"


def test_search_ignores_namespace(env):
    j = env.get(S, params={"q": "stone"}).json()
    keys = [s["key"] for s in j["stats"]]
    assert keys[0] == "minecraft:stone" and "minecraft:cobblestone" in keys
    assert keys.index("minecraft:stone") < keys.index("minecraft:cobblestone")
    assert env.get(S, params={"q": "minecraft:sto"}).json()["stats"][0]["key"] == "minecraft:stone"
    assert j["stats"][0]["category"] == "minecraft:mined"


def test_search_excludes_mirror_objectives(env):
    j = env.get(S, params={"q": "stone"}).json()
    assert j["objectives"] == []
    assert env.get(S, params={"q": "kill"}).json()["objectives"] == [
        {"objective": "kills", "servers": ["smp"]}]


def test_search_excludes_fake_players(env):
    # Botty is in no team on smp -> bot, so not counted anywhere
    assert env.get(S, params={"q": "botty"}).json()["players"] == []
    assert env.get(S, params={"q": "alice"}).json()["players"][0] == {"uuid": U1, "name": "Alice"}
    assert env.get(S, params={"q": "bot"}).json()["players"] == []


def test_search_short_q_and_limit(env):
    assert env.get(S, params={"q": ""}).json() == {"players": [], "stats": [], "objectives": []}
    assert len(env.get(S, params={"q": "s", "limit": 2}).json()["stats"]) == 2
    assert env.get(S, params={"q": "s", "limit": 500}).status_code == 200


def test_search_uuid_prefix_for_unnamed_player(env):
    u = "55555555-5555-5555-5555-555555555555"
    sql(env.world, "INSERT INTO players (uuid, name, name_resolved, first_seen, "
        "last_observed) VALUES (?, NULL, 0, 0, 0)", (u,))
    sql(env.world, "INSERT INTO player_flags VALUES ('smp', ?, 0, 0)", (u,))
    sql(env.world, "INSERT INTO stat_current VALUES ('smp', ?, 'minecraft:mined', "
        "'minecraft:stone', 1, 0, 0)", (u,))
    sql(env.world, "INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, "
        "rows_written) VALUES (1000100, 'smp', 'stats', 1, 1, 1)")
    j = env.get(S, params={"q": "55555555"}).json()
    assert j["players"] == [{"uuid": u, "name": None}]
    assert env.get(S, params={"q": "5555"}).json()["players"] == []


def test_index_rebuilt_on_new_version(env):
    assert env.get(S, params={"q": "zed"}).json()["players"] == []
    sql(env.world, "INSERT INTO players (uuid, name, name_resolved, first_seen, "
        "last_observed) VALUES (?, 'Zed', 1, 0, 0)", ("66666666-6666-6666-6666-666666666666",))
    sql(env.world, "INSERT INTO player_flags VALUES ('smp', "
        "'66666666-6666-6666-6666-666666666666', 0, 0)")
    sql(env.world, "INSERT INTO stat_current VALUES ('smp', "
        "'66666666-6666-6666-6666-666666666666', 'minecraft:mined', 'minecraft:stone', 1, 0, 0)")
    # same version: still the old in-memory index
    assert env.get(S, params={"q": "zed"}).json()["players"] == []
    sql(env.world, "INSERT INTO snapshot_runs (at, server_key, kind, ok, entries_read, "
        "rows_written) VALUES (1000100, 'smp', 'stats', 1, 1, 1)")
    assert [p["name"] for p in env.get(S, params={"q": "zed"}).json()["players"]] == ["Zed"]


def test_keys_stat(env):
    j = env.get("/api/stats/keys?kind=stat&scope=smp").json()
    assert j["stats"]["minecraft:mined"] == [
        "minecraft:cobblestone", "minecraft:deepslate", "minecraft:stone"]
    assert j["stats"]["minecraft:custom"] == ["minecraft:play_time"]


def test_keys_scoreboard_marks_removed(env):
    sql(env.world, "UPDATE scoreboard_meta SET removed=1 WHERE objective='deaths'")
    j = env.get("/api/stats/keys?kind=scoreboard&scope=smp").json()
    assert j["objectives"] == [{"objective": "deaths", "removed": True},
                               {"objective": "kills", "removed": False}]
    # removed objectives are not searchable
    assert env.get(S, params={"q": "deaths"}).json()["objectives"] == []


def test_keys_bad_args(env):
    assert env.get("/api/stats/keys?kind=nope").status_code == 400
    assert env.get("/api/stats/keys?scope=nope").status_code == 400
