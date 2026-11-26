"""Player heads: <website>/cache/heads/<uuid>.png from the Mojang skin (bot/heads.py)."""
import base64
import io
import json
import sqlite3

import pytest
from PIL import Image

import heads
from api.settings import load_settings

REAL = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
REAL2 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
FAKE = "aaaaaaaa-aaaa-3aaa-8aaa-aaaaaaaaaaaa"
SKIN_URL = "http://textures.minecraft.net/texture/abc"
FACE = (200, 10, 10, 255)
HAT = (10, 200, 10, 255)


def skin_png(size=(64, 64), hat_pixel=True, hat_opaque=False):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(8, 16):
        for y in range(8, 16):
            img.putpixel((x, y), FACE)
    if hat_opaque:
        for x in range(40, 48):
            for y in range(8, 16):
                img.putpixel((x, y), HAT)
    elif hat_pixel:
        img.putpixel((40, 8), HAT)  # top-left of the hat layer
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def profile_body(url=SKIN_URL):
    tex = {"textures": {"SKIN": {"url": url}} if url else {}}
    value = base64.b64encode(json.dumps(tex).encode()).decode()
    return json.dumps({"id": "x", "name": "n",
                       "properties": [{"name": "textures", "value": value}]}).encode()


def test_face_has_hat_over_face_scaled_nearest():
    img = heads.face_from_skin(skin_png())
    assert img.size == (64, 64) and img.mode == "RGBA"
    assert img.getpixel((0, 0)) == HAT          # hat pixel covers face (0,0), 8x8 block
    assert img.getpixel((7, 7)) == HAT
    assert img.getpixel((8, 0)) == FACE
    assert img.getpixel((63, 63)) == FACE


def test_legacy_skin_with_fully_opaque_hat_ignores_the_hat():
    # 64x32 skins: the game drops a hat layer with no transparent pixel
    img = heads.face_from_skin(skin_png(size=(64, 32), hat_opaque=True))
    assert img.getpixel((0, 0)) == FACE and img.getpixel((40, 40)) == FACE


def test_modern_skin_keeps_an_opaque_hat():
    img = heads.face_from_skin(skin_png(hat_opaque=True))
    assert img.getpixel((40, 40)) == HAT


def test_bad_skin_raises():
    with pytest.raises(ValueError):
        heads.face_from_skin(b"not a png")
    buf = io.BytesIO()
    Image.new("RGBA", (16, 16)).save(buf, "PNG")
    with pytest.raises(ValueError):
        heads.face_from_skin(buf.getvalue())


# ---------------------------------------------------------------- updater

@pytest.fixture
def db(tmp_path):
    import snapshot
    conn = snapshot.connect_db(str(tmp_path / "s.db"))
    for u, n in ((REAL, "Realo"), (REAL2, "Other"), (FAKE, "Fakey")):
        conn.execute("INSERT INTO players (uuid, name, first_seen, last_observed) "
                     "VALUES (?, ?, 1, 1)", (u, n))
    conn.commit()
    yield conn
    conn.close()


class Fetch:
    def __init__(self, profiles=None, skin=None):
        self.calls = []
        self.profiles = profiles or {}
        self.skin = skin if skin is not None else (200, skin_png())

    def __call__(self, method, url, body=None):
        self.calls.append(url)
        if url.startswith(heads.PROFILE_URL):
            hexid = url[len(heads.PROFILE_URL):]
            return self.profiles.get(hexid, (200, profile_body()))
        return self.skin


def hx(u):
    return u.replace("-", "")


def cfg(**over):
    return load_settings({"heads": over} if over else None).heads


def run(db, out, fetch, now=1_000_000, **over):
    return heads.update_heads(db, str(out), now, fetch, cfg(**over), clock=lambda: 0.0)


def test_writes_real_players_only(db, tmp_path):
    out = tmp_path / "heads"
    f = Fetch()
    r = run(db, out, f)
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{REAL}.png", f"{REAL2}.png"])
    assert not any(hx(FAKE) in c for c in f.calls)
    assert r["written"] == 2
    assert Image.open(out / f"{REAL}.png").size == (64, 64)


def test_no_second_check_before_refresh(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    f = Fetch()
    run(db, out, f, now=1_000_000 + 3600)
    assert f.calls == []


def test_refresh_same_skin_url_skips_the_download(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    f = Fetch()
    run(db, out, f, now=1_000_000 + 25 * 3600)
    assert len(f.calls) == 2 and all(c.startswith(heads.PROFILE_URL) for c in f.calls)


def test_missing_file_is_written_again(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    (out / f"{REAL}.png").unlink()
    f = Fetch()
    run(db, out, f, now=1_000_000 + 60)
    assert (out / f"{REAL}.png").exists() and len(f.calls) == 2


def test_budget_new_players_first(db, tmp_path):
    out = tmp_path / "heads"
    # REAL checked long ago, REAL2 never: REAL2 goes first
    db.execute("INSERT INTO player_heads (uuid, checked_at, skin_url) VALUES (?, 1, NULL)", (REAL,))
    f = Fetch()
    run(db, out, f, max_calls=1)
    assert [c for c in f.calls if c.startswith(heads.PROFILE_URL)] == [heads.PROFILE_URL + hx(REAL2)]


def test_rate_limit_stops_the_run(db, tmp_path):
    out = tmp_path / "heads"
    f = Fetch(profiles={hx(REAL): (429, b""), hx(REAL2): (429, b"")})
    r = run(db, out, f)
    assert len(f.calls) == 1 and r["stopped"] == "rate limited"
    assert db.execute("SELECT COUNT(*) FROM player_heads").fetchone()[0] == 0


def test_network_error_stops_without_marking(db, tmp_path):
    out = tmp_path / "heads"
    r = run(db, out, Fetch(profiles={hx(REAL): (0, b""), hx(REAL2): (0, b"")}))
    assert r["stopped"] and db.execute("SELECT COUNT(*) FROM player_heads").fetchone()[0] == 0


def test_no_skin_removes_the_file(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    f = Fetch(profiles={hx(REAL): (200, profile_body(url=None))})
    run(db, out, f, now=1_000_000 + 25 * 3600)
    assert not (out / f"{REAL}.png").exists() and (out / f"{REAL2}.png").exists()


@pytest.mark.parametrize("answer", [(500, b""), (503, b"busy"), (403, b""), (200, b"<html>")])
def test_server_error_keeps_the_head_and_stops(db, tmp_path, answer):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    later = 1_000_000 + 25 * 3600
    f = Fetch(profiles={hx(REAL): answer, hx(REAL2): answer})
    r = run(db, out, f, now=later)
    assert r["stopped"] and len(f.calls) == 1
    assert (out / f"{REAL}.png").exists() and (out / f"{REAL2}.png").exists()
    # not marked: still due next run
    assert db.execute("SELECT COUNT(*) FROM player_heads WHERE checked_at = ?",
                      (later,)).fetchone()[0] == 0


def test_skin_server_error_keeps_the_head(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    (out / f"{REAL}.png").unlink()
    f = Fetch(skin=(503, b""))
    r = run(db, out, f, now=1_000_000 + 60)
    assert r["stopped"]
    row = db.execute("SELECT skin_url FROM player_heads WHERE uuid = ?", (REAL,)).fetchone()
    assert row[0] == SKIN_URL  # unchanged, so it is retried


def test_each_player_committed(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    assert not db.in_transaction


def test_skipped_uuids_get_no_head(db, tmp_path):
    out = tmp_path / "heads"
    f = Fetch()
    heads.update_heads(db, str(out), 1_000_000, f, cfg(), clock=lambda: 0.0, skip={REAL2})
    assert not any(hx(REAL2) in c for c in f.calls)
    assert not (out / f"{REAL2}.png").exists()


def test_skipped_uuid_file_removed(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    heads.update_heads(db, str(out), 1_000_060, Fetch(), cfg(), clock=lambda: 0.0, skip={REAL2})
    assert not (out / f"{REAL2}.png").exists() and (out / f"{REAL}.png").exists()


def test_unknown_profile_marks_checked(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch(profiles={hx(REAL): (204, b""), hx(REAL2): (404, b"")}))
    assert db.execute("SELECT COUNT(*) FROM player_heads WHERE skin_url IS NULL").fetchone()[0] == 2
    assert not out.exists() or not any(out.iterdir())


def test_blocked_player_file_removed_and_not_fetched(db, tmp_path):
    out = tmp_path / "heads"
    run(db, out, Fetch())
    db.execute("INSERT INTO blocked_players (uuid, name, term) VALUES (?, 'x', 'x')", (REAL,))
    f = Fetch()
    run(db, out, f, now=1_000_000 + 25 * 3600)
    assert not (out / f"{REAL}.png").exists()
    assert not any(hx(REAL) in c for c in f.calls)


def test_snapshot_skips_merged_away_accounts(world, run_snapshot, monkeypatch):
    from api.merges import parse_merges
    world.config["server"]["website_path"] = str(world.root / "www")
    world.set_usercache("cmp", {REAL: "Realo", REAL2: "Alt"})
    for u in (REAL, REAL2):
        world.add_stats("cmp", u, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1_000_000, mojang_fetch=Fetch())   # before the merge: both get a head
    d = world.root / "www" / "cache" / "heads"
    assert (d / f"{REAL2}.png").exists()
    merges = parse_merges({"merges": [{"from": REAL2, "to": REAL, "names": ["Alt"]}]})
    f = Fetch()
    run_snapshot(1_000_000 + 25 * 3600, mojang_fetch=f, merges=merges)
    assert (d / f"{REAL}.png").exists() and not (d / f"{REAL2}.png").exists()
    assert not any(hx(REAL2) in c for c in f.calls)


def test_broken_skin_marks_checked_without_file(db, tmp_path):
    out = tmp_path / "heads"
    r = run(db, out, Fetch(skin=(200, b"garbage")))
    assert r["written"] == 0
    assert db.execute("SELECT COUNT(*) FROM player_heads").fetchone()[0] == 2


def test_wall_clock_cap(db, tmp_path):
    out = tmp_path / "heads"
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    f = Fetch()
    heads.update_heads(db, str(out), 1_000_000, f, cfg(max_seconds=30),
                       clock=lambda: next(ticks))
    assert len([c for c in f.calls if c.startswith(heads.PROFILE_URL)]) == 1


def test_disabled_does_nothing(db, tmp_path):
    f = Fetch()
    r = run(db, tmp_path / "heads", f, enabled=False)
    assert f.calls == [] and r["written"] == 0


def test_settings_validated():
    with pytest.raises(ValueError, match="heads.max_calls"):
        load_settings({"heads": {"max_calls": -1}})


def test_snapshot_writes_heads_under_website_path(world, run_snapshot):
    world.config["server"]["website_path"] = str(world.root / "www")
    world.set_usercache("cmp", {REAL: "Realo"})
    world.add_stats("cmp", REAL, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1_000_000, mojang_fetch=Fetch())
    assert (world.root / "www" / "cache" / "heads" / f"{REAL}.png").exists()


def test_snapshot_without_website_path_skips_heads(world, run_snapshot):
    world.set_usercache("cmp", {REAL: "Realo"})
    world.add_stats("cmp", REAL, {"minecraft:mined": {"minecraft:stone": 1}})
    run_snapshot(1_000_000, mojang_fetch=Fetch())
    c = sqlite3.connect(world.db_path)
    assert c.execute("SELECT COUNT(*) FROM player_heads").fetchone()[0] == 0
    c.close()


def test_ipv4_connection_resolves_ipv4_only(monkeypatch):
    """Mojang's session server rate-limits the live server's IPv6 (shared
    with the MC servers) but not its IPv4 (2026-11-26)."""
    import socket
    asked = []

    def fake_getaddrinfo(host, port, family=0, type=0, *a, **k):
        asked.append(family)
        return []
    monkeypatch.setattr(heads.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(OSError):
        heads._ipv4_create_connection(("sessionserver.mojang.com", 443), 1)
    assert asked == [socket.AF_INET]


def test_snapshot_heads_use_the_ipv4_fetch_by_default(monkeypatch, tmp_path):
    import snapshot
    made = []
    monkeypatch.setattr(heads, "ipv4_fetch", lambda timeout: made.append(timeout) or Fetch())
    settings = load_settings({})
    c = sqlite3.connect(":memory:")
    snapshot.run_heads(c, {"server": {"website_path": str(tmp_path)}}, settings, 1)
    assert made == [settings.heads.timeout_s]
    made.clear()
    off = load_settings({"heads": {"ipv4_only": False}})
    monkeypatch.setattr(snapshot, "default_fetch", lambda *a, **k: (0, b""))
    snapshot.run_heads(c, {"server": {"website_path": str(tmp_path)}}, off, 1)
    assert made == []
