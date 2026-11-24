import pytest
import snapshot
from api import scope as sc


@pytest.fixture
def conn(tmp_path):
    c = snapshot.connect_db(str(tmp_path / "t.db"))
    c.executemany(
        "INSERT OR REPLACE INTO servers(key,label,group_key,filters_bots,provisioned) VALUES(?,?,?,?,?)",
        [("smp", "SMP", "smp", 1, 1), ("cmp", "CMP", "cmp", 0, 1),
         ("cmp2", "CMP2", "cmp", 0, 1), ("ghost", "Ghost", "cmp", 0, 0),
         ("all", "All", "all", 0, 0)])
    c.execute("INSERT INTO player_flags VALUES('smp','R',0,1)")
    c.execute("INSERT INTO player_flags VALUES('smp','B',1,1)")
    c.execute("INSERT INTO player_flags VALUES('cmp','B',1,1)")
    for s, u in [("smp", "R"), ("smp", "B"), ("smp", "M"), ("cmp", "B"), ("cmp", "R")]:
        c.execute("INSERT INTO stat_current VALUES(?,?,?,?,?,?,?)",
                  (s, u, "minecraft:custom", "minecraft:jump", 1, 1, 1))
    c.commit()
    yield c
    c.close()


def rows(conn, scope_name, include_bots):
    s = sc.resolve_scope(conn, scope_name)
    frag, params = sc.stat_filter_sql(s, include_bots, "sc")
    return {(r[0], r[1]) for r in conn.execute(
        f"SELECT server_key, uuid FROM stat_current sc WHERE {frag}", params)}


def test_cmps_scope_from_group_key(conn):
    assert sc.resolve_scope(conn, "cmps").servers == ("cmp", "cmp2")


def test_unprovisioned_excluded_from_all(conn):
    assert sc.resolve_scope(conn, "all").servers == ("cmp", "cmp2", "smp")
    with pytest.raises(ValueError):
        sc.resolve_scope(conn, "ghost")


def test_smp_bot_excluded_by_default(conn):
    assert ("smp", "B") not in rows(conn, "smp", False)
    assert ("smp", "B") in rows(conn, "smp", True)


def test_smp_missing_flag_is_bot(conn):
    assert ("smp", "M") not in rows(conn, "smp", False)
    assert ("smp", "M") in rows(conn, "smp", True)
    assert ("smp", "R") in rows(conn, "smp", False)


def test_bot_still_counted_on_cmp(conn):
    r = rows(conn, "all", False)
    assert ("cmp", "B") in r and ("smp", "B") not in r


def test_is_counted(conn):
    assert not sc.is_counted(conn, "smp", "B")
    assert not sc.is_counted(conn, "smp", "M")
    assert sc.is_counted(conn, "smp", "B", include_bots=True)
    assert sc.is_counted(conn, "smp", "R")
    assert sc.is_counted(conn, "cmp", "B")


def test_unresolved_participant_never_returned(conn):
    conn.executemany(
        "INSERT INTO scoreboard_snapshots(at,server_key,participant_kind,uuid,raw_name,objective,value) VALUES(1,?,?,?,?,?,1)",
        [("smp", "player", "R", "R", "obj"), ("smp", "unresolved", None, "Foo", "obj")])
    conn.executemany("INSERT INTO scoreboard_current VALUES(?,?,?,1,1,1)",
                     [("smp", "R", "obj"), ("smp", "unresolved:Foo", "obj")])
    conn.commit()
    s = sc.resolve_scope(conn, "smp")
    f, p = sc.scoreboard_filter_sql(s, True, "x", is_current=False)
    assert [r[0] for r in conn.execute(
        f"SELECT raw_name FROM scoreboard_snapshots x WHERE {f}", p)] == ["R"]
    f, p = sc.scoreboard_filter_sql(s, True, "x", is_current=True)
    assert [r[0] for r in conn.execute(
        f"SELECT participant_key FROM scoreboard_current x WHERE {f}", p)] == ["R"]


def test_mirror_objective_excluded(conn):
    conn.executemany("INSERT INTO scoreboard_meta VALUES('smp',?,?,1,1,0)",
                     [("real", 0), ("mirror", 1)])
    conn.executemany("INSERT INTO scoreboard_current VALUES('smp','R',?,1,1,1)",
                     [("real",), ("mirror",)])
    conn.commit()
    s = sc.resolve_scope(conn, "smp")
    f, p = sc.scoreboard_filter_sql(s, True, "x", is_current=True)
    assert [r[0] for r in conn.execute(
        f"SELECT objective FROM scoreboard_current x WHERE {f}", p)] == ["real"]


def test_unknown_scope_raises(conn):
    with pytest.raises(ValueError):
        sc.resolve_scope(conn, "nope")
