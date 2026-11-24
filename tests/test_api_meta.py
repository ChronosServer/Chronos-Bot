import sqlite3

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from api import cache, db
from api.main import create_app
from api.scope import resolve_scope

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client(world, run_snapshot, monkeypatch):
    world.add_stats("smp", U1, {"minecraft:custom": {"minecraft:play_time": 10}})
    world.add_stats("cmp", U2, {"minecraft:mined": {"minecraft:stone": 5}})
    world.set_usercache("smp", {U1: "Alice"})
    run_snapshot(1_000_000)
    world.add_stats("smp", U1, {"minecraft:custom": {"minecraft:play_time": 20}})
    run_snapshot(1_000_600)
    monkeypatch.setenv("CHRONOS_STATS_DB", world.db_path)
    cache.clear()
    with TestClient(create_app()) as c:
        c.run_snapshot = run_snapshot
        yield c
    cache.clear()


def test_meta_shape(client):
    r = client.get("/api/stats/meta")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"data_version", "last_run_at", "servers", "scopes",
                      "categories", "tracking_started_at"}
    assert [s["key"] for s in j["servers"]] == ["cmp", "cmp2", "smp"]
    assert set(j["servers"][0]) == {"key", "label", "group", "filters_bots",
                                    "track_stats", "track_scoreboards"}
    assert j["scopes"][:3] == ["all", "smp", "cmps"]
    assert "cmp2" in j["scopes"] and len(set(j["scopes"])) == len(j["scopes"])
    assert "minecraft:custom" in j["categories"]
    assert j["last_run_at"] == 1_000_600
    assert j["tracking_started_at"]["smp"] == 1_000_000
    assert j["data_version"] != "0"
    assert r.headers["Cache-Control"] == "public, max-age=60, stale-while-revalidate=900"
    assert r.headers["ETag"].startswith('"' + j["data_version"] + "-")


def test_etag_304(client):
    r1 = client.get("/api/stats/meta")
    r2 = client.get("/api/stats/meta", headers={"If-None-Match": r1.headers["ETag"]})
    assert r2.status_code == 304 and r2.content == b""
    assert r2.headers["ETag"] == r1.headers["ETag"]


def test_cache_invalidated_by_new_run(client):
    e1 = client.get("/api/stats/meta").headers["ETag"]
    client.run_snapshot(1_001_200)
    r = client.get("/api/stats/meta", headers={"If-None-Match": e1})
    assert r.status_code == 200 and r.headers["ETag"] != e1


def test_db_is_read_only(client):
    conn = db.get_conn()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO meta VALUES('x','y')")
    finally:
        conn.close()


def test_bad_scope_400(client):
    router = APIRouter()

    @router.get("/_boom")
    async def boom(conn=Depends(db.get_db)):
        resolve_scope(conn, "nope")

    app = create_app()
    app.include_router(router, prefix="/api/stats")
    r = TestClient(app).get("/api/stats/_boom")
    assert r.status_code == 400 and "nope" in r.json()["error"]
