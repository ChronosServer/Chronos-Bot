"""Multi-server scopes whose servers' live tracking started a few seconds
apart (live: SMP 1795528256, CMPs 1795528261). The window must start where
every server has a value, or a later-starting server's whole total counts
as gain."""
import pytest
from fastapi.testclient import TestClient

from api import cache
from api.main import create_app

P = "11111111-1111-1111-1111-111111111111"
Q = "22222222-2222-2222-2222-222222222222"  # idle
DAY = 86400
L = 1_800_000_000
A = "/api/stats"
PT = "kind=stat&category=minecraft:custom&key=minecraft:play_time"


def pt(n):
    return {"minecraft:custom": {"minecraft:play_time": n}}


@pytest.fixture
def env(world, run_snapshot, monkeypatch):
    # smp only at L (cmp's world has no stat files yet), cmp joins 5 s later
    world.set_usercache("smp", {P: "Pat", Q: "Quinn"})
    world.set_usercache("cmp", {P: "Pat"})
    world.set_scoreboard("smp", [], [], {"t": ["Pat", "Quinn"]})
    world.add_stats("smp", P, pt(1000))
    world.add_stats("smp", Q, pt(7))
    run_snapshot(L)
    world.add_stats("cmp", P, pt(1_000_000))      # big existing CMP total
    run_snapshot(L + 5)
    world.add_stats("smp", P, pt(1100))           # real gains: +100 smp, +50 cmp
    world.add_stats("cmp", P, pt(1_000_050))
    run_snapshot(L + DAY)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    monkeypatch.setattr("time.time", lambda: L + 2 * DAY)
    cache.clear()
    with TestClient(create_app()) as c:
        yield c
    cache.clear()


def lb(c, qs):
    j = c.get(f"{A}/leaderboard?{qs}").json()
    return j["effective_from"], {r["uuid"]: r["value"] for r in j["rows"]}


@pytest.mark.parametrize("scope,gain,start", [("all", 150, L + 5), ("cmps", 50, L + 5),
                                              ("smp", 100, L)])
def test_leaderboard_delta_counts_only_real_gains(env, scope, gain, start):
    eff, rows = lb(env, f"{PT}&scope={scope}&range=1w")
    assert eff == start
    assert rows == {P: gain}


def test_compare_series_totals_agree(env):
    j = env.get(f"{A}/compare?uuids={P},{Q}&keys=minecraft:custom|minecraft:play_time"
                f"&scope=all&range=1w").json()
    assert j["effective_from"] == L + 5
    assert j["values"]["minecraft:custom|minecraft:play_time"] == {P: 150, Q: 0}
    s = j["series"]["minecraft:custom|minecraft:play_time"][P]
    assert s[0] == [L + 5, 1_001_000] and s[-1][1] - s[0][1] == 150
    s = env.get(f"{A}/series?{PT}&uuids={P}&scope=all&range=1w").json()["series"][P]
    assert s[-1][1] - s[0][1] == 150
    t = env.get(f"{A}/totals?{PT}&scopes=all,smp,cmps&range=1w").json()
    assert t["effective_from"] == L + 5
    gains = {k: v[-1][1] - v[0][1] for k, v in t["series"].items()}
    assert gains == {"all": 150, "smp": 100, "cmps": 50}


def test_range_all_on_multi_server_scope_starts_when_all_are_tracked(env):
    eff, rows = lb(env, f"{PT}&scope=all&range=all")
    assert eff == L + 5 and rows == {P: 150}
    eff, rows = lb(env, f"{PT}&scope=smp&range=all")
    assert eff == L and rows == {P: 100}  # gain since tracking began
