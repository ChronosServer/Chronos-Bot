import sqlite3

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api import cache, db
from api.cache import cached
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


def test_db_connection_pragmas(client):
    conn = db.get_conn()
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        conn.close()


def test_db_uri_is_read_only_independent_of_pragma(client):
    conn = db.get_conn()
    try:
        conn.execute("PRAGMA query_only=0")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO meta VALUES('x','y')")
    finally:
        conn.close()


def _app_with(handler, path="/_t"):
    router = APIRouter()
    router.get(path)(handler)
    app = create_app()
    app.include_router(router, prefix="/api/stats")
    return TestClient(app)


def test_cached_requires_request_and_conn():
    with pytest.raises(TypeError, match="request"):
        @cached
        def h(conn=Depends(db.get_db)): return {}
    with pytest.raises(TypeError, match="conn"):
        @cached
        def h2(request: Request): return {}


def test_cached_sync_handler_hits_cache_once(client):
    calls = []

    @cached
    def h(request: Request, conn=Depends(db.get_db)):
        calls.append(1)
        return {"n": 1, 1: "x"}

    c = _app_with(h)
    assert c.get("/api/stats/_t?a=1&b=2").json() == {"n": 1, "1": "x"}
    c.get("/api/stats/_t?b=2&a=1")
    assert len(calls) == 1
    c.get("/api/stats/_t?a=2&b=2")
    assert len(calls) == 2


def test_cached_async_handler_works(client):
    @cached
    async def h(request: Request, conn=Depends(db.get_db)):
        return {"ok": True}

    assert _app_with(h).get("/api/stats/_t").json() == {"ok": True}


def test_cached_non_200_response_not_cached(client):
    calls = []

    @cached
    def h(request: Request, conn=Depends(db.get_db)):
        calls.append(1)
        return JSONResponse({"error": "x"}, status_code=404)

    c = _app_with(h)
    assert c.get("/api/stats/_t").status_code == 404
    assert c.get("/api/stats/_t").status_code == 404
    assert len(calls) == 2


def test_bad_scope_400(client):
    router = APIRouter()

    @router.get("/_boom")
    async def boom(conn=Depends(db.get_db)):
        resolve_scope(conn, "nope")

    app = create_app()
    app.include_router(router, prefix="/api/stats")
    r = TestClient(app).get("/api/stats/_boom")
    assert r.status_code == 400 and "nope" in r.json()["error"]
