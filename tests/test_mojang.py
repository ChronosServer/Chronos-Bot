import json

import pytest
import snapshot
from mojang import MojangClient

DAY = 86400
NOW = 1_000_000


@pytest.fixture
def conn(tmp_path):
    c = snapshot.connect_db(str(tmp_path / "m.db"))
    yield c
    c.close()


def make_fetch(calls, status=200, known=None):
    known = known or {}

    def fetch(method, url, body):
        calls.append((method, url, body))
        if status != 200:
            return status, b""
        names = json.loads(body)
        out = [{"id": known[n.lower()].replace("-", ""), "name": n.title()}
               for n in names if n.lower() in known]
        return 200, json.dumps(out).encode()
    return fetch


def test_names_to_uuids_batches_of_10(conn):
    calls = []
    c = MojangClient(make_fetch(calls), conn, NOW)
    c.names_to_uuids([f"n{i}" for i in range(23)])
    assert len(calls) == 3
    assert all(m == "POST" for m, _, _ in calls)
    assert [len(json.loads(b)) for _, _, b in calls] == [10, 10, 3]


def test_resolves_and_dashes(conn):
    u = "12345678-1234-1234-1234-123456789abc"
    c = MojangClient(make_fetch([], known={"alice": u}), conn, NOW)
    assert c.names_to_uuids(["Alice", "Nobody"]) == {"alice": (u, "Alice")}


def test_lookup_cached_7_days(conn):
    calls = []
    f = make_fetch(calls)
    MojangClient(f, conn, NOW).names_to_uuids(["a", "b"])
    assert len(calls) == 1
    MojangClient(f, conn, NOW + DAY).names_to_uuids(["a", "b"])
    assert len(calls) == 1
    MojangClient(f, conn, NOW + 8 * DAY).names_to_uuids(["a", "b"])
    assert len(calls) == 2


@pytest.mark.parametrize("status", [429, 0, 500])
def test_mojang_down_returns_empty(conn, status):
    calls = []
    c = MojangClient(make_fetch(calls, status=status), conn, NOW)
    assert c.names_to_uuids(["a"]) == {}
    row = conn.execute("SELECT result, checked_at FROM name_lookups WHERE lookup_key='name:a'").fetchone()
    assert row == (None, NOW)
    assert MojangClient(make_fetch(calls), conn, NOW + DAY).names_to_uuids(["a"]) == {}
    assert len(calls) == 1


def test_budget_respected(conn):
    calls = []
    c = MojangClient(make_fetch(calls), conn, NOW, budget=2)
    c.names_to_uuids([f"n{i}" for i in range(50)])
    assert len(calls) == 2


def test_uuid_to_name(conn):
    u = "12345678-1234-1234-1234-123456789abc"
    calls = []

    def fetch(method, url, body):
        calls.append((method, url))
        return 200, json.dumps({"id": u.replace("-", ""), "name": "Alice"}).encode()
    c = MojangClient(fetch, conn, NOW)
    assert c.uuid_to_name(u) == "Alice"
    assert calls[0] == ("GET", "https://sessionserver.mojang.com/session/minecraft/profile/" + u.replace("-", ""))
    assert c.uuid_to_name(u) == "Alice"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [204, 404, 429, 0])
def test_uuid_to_name_not_found_or_failed(conn, status):
    c = MojangClient(lambda m, u, b: (status, b""), conn, NOW)
    assert c.uuid_to_name("12345678-1234-1234-1234-123456789abc") is None
    assert conn.execute("SELECT result FROM name_lookups").fetchone() == (None,)


def test_canonical_name_stored_and_cached(conn):
    u = "12345678-1234-1234-1234-123456789abc"
    calls = []
    f = make_fetch(calls, known={"alice": u})
    assert MojangClient(f, conn, NOW).names_to_uuids(["aLICE"]) == {"alice": (u, "Alice")}
    assert MojangClient(f, conn, NOW + DAY).names_to_uuids(["alice"]) == {"alice": (u, "Alice")}
    assert len(calls) == 1


def test_invalid_names_never_fetched(conn):
    calls = []
    c = MojangClient(make_fetch(calls), conn, NOW)
    c.names_to_uuids(["bad name", "x" * 17, "", "ok_1"])
    assert len(calls) == 1
    assert json.loads(calls[0][2]) == ["ok_1"]
    assert conn.execute("SELECT COUNT(*) FROM name_lookups").fetchone() == (1,)
    assert c.calls == 1


def test_budget_shared_and_failures_consume(conn):
    calls = []
    c = MojangClient(lambda m, u, b: (calls.append(1) or (429, b"")), conn, NOW, budget=2)
    c.uuid_to_name("12345678-1234-1234-1234-123456789abc")
    c.names_to_uuids(["a"])
    assert len(calls) == 2
    assert c.uuid_to_name("12345678-1234-1234-1234-123456789abd") is None
    c.names_to_uuids(["b"])
    assert len(calls) == 2


def test_budget_exhausted_names_left_uncached(conn):
    c = MojangClient(make_fetch([]), conn, NOW, budget=1)
    c.names_to_uuids([f"n{i}" for i in range(15)])
    keys = {r[0] for r in conn.execute("SELECT lookup_key FROM name_lookups")}
    assert len(keys) == 10 and "name:n12" not in keys


def test_uuid_key_normalised(conn):
    c = MojangClient(lambda m, u, b: (404, b""), conn, NOW)
    c.uuid_to_name("12345678123412341234123456789ABC")
    assert conn.execute("SELECT lookup_key FROM name_lookups").fetchone() == (
        "uuid:12345678-1234-1234-1234-123456789abc",)
