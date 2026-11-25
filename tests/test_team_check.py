import json

import pytest
import snapshot
from mojang import MojangClient

NOW = 5_000_000
A = "aaaaaaaa-0000-0000-0000-000000000001"
BED = "bbbbbbbb-0000-0000-0000-000000000002"
OTHER = "cccccccc-0000-0000-0000-000000000003"


@pytest.fixture
def conn(world):
    c = snapshot.connect_db(world.db_path)
    yield c
    c.close()


def flags(conn):
    return dict(conn.execute("SELECT uuid, is_bot FROM player_flags WHERE server_key='smp'"))


def check(world, conn, usercache=None, mojang=None):
    snapshot.run_team_check(conn, "smp", str(world.world_dir("smp")), NOW,
                            usercache or {}, mojang)


def fake_mojang(conn, known):
    calls = []

    def fetch(method, url, body):
        calls.append(body)
        names = json.loads(body)
        return 200, json.dumps([{"id": known[n.lower()].replace("-", ""), "name": n.title()}
                                for n in names if n.lower() in known]).encode()
    return MojangClient(fetch, conn, NOW), calls


def test_team_check_only_smp_stat_holders(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    snapshot.upsert_player(conn, OTHER, "Alice", 1, True)
    check(world, conn, {A: "Alice"})
    assert flags(conn) == {A: 0}


def test_team_member_with_unknown_name_resolved_and_real(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    m, calls = fake_mojang(conn, {"alice": A})
    check(world, conn, {}, m)
    assert flags(conn) == {A: 0}
    assert conn.execute("SELECT name, name_resolved FROM players WHERE uuid=?", (A,)).fetchone() == ("Alice", 1)


def test_invalid_team_entry_does_not_poison_valid_ones(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["alice", "not a name!"]})
    m, calls = fake_mojang(conn, {"alice": A})
    check(world, conn, {}, m)
    assert flags(conn) == {A: 0}
    assert conn.execute("SELECT name FROM players WHERE uuid=?", (A,)).fetchone() == ("Alice",)


def test_unteamed_named_player_is_bot(world, conn):
    world.add_stats("smp", BED, {})
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    check(world, conn, {BED: "BedBot", A: "Alice"})
    assert flags(conn) == {BED: 1, A: 0}


def test_team_check_case_insensitive(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["aLiCe"]})
    check(world, conn, {A: "ALICE"})
    assert flags(conn) == {A: 0}


def test_nameless_stat_holder_is_bot(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    check(world, conn, {})
    assert flags(conn) == {A: 1}


def test_known_names_not_sent_to_mojang(world, conn):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    m, calls = fake_mojang(conn, {})
    check(world, conn, {A: "Alice"}, m)
    assert calls == []


def test_run_once_uses_injected_fetch(world, run_snapshot):
    world.add_stats("smp", A, {})
    world.set_scoreboard("smp", [], [], {"t": ["Alice"]})
    seen = []

    def fetch(method, url, body):
        seen.append(url)
        return 200, json.dumps([{"id": A.replace("-", ""), "name": "Alice"}]).encode()
    run_snapshot(NOW, mojang_fetch=fetch)
    c = snapshot.connect_db(world.db_path)
    try:
        assert dict(c.execute("SELECT uuid, is_bot FROM player_flags")) == {A: 0}
    finally:
        c.close()
    assert len(seen) == 1
