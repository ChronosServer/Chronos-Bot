"""Player heads cached on our server: <server.website_path>/cache/heads/<uuid>.png.

The 8x8 face plus the hat layer of the player's skin, scaled to 64x64
(nearest neighbour). Skin url from the Mojang session server profile. Runs as
the last step of every snapshot run, bounded by stats_tracking.heads
(max_calls profile lookups, max_seconds): players never checked go first,
then each one again every refresh_hours. A rate limit (429) or a network
error ends the run's step; the rest wait for the next run. No file for fake
players (version-3 uuids), blocked players (slur filter; an existing file is
removed) or profiles without a skin: the site falls back on 404.
State per uuid in player_heads (migration v6). Never raises on network
trouble; the caller still guards it (a failure here must not fail the run).
"""
import base64
import io
import json
import os
import time

from PIL import Image, UnidentifiedImageError

from api.scope import is_fake_uuid

PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"
SIZE = 64


def face_from_skin(data: bytes) -> Image.Image:
    """64x64 RGBA head (face + hat) from skin PNG bytes; ValueError if not a skin."""
    try:
        skin = Image.open(io.BytesIO(data))
        skin.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise ValueError(f"not an image: {e}") from None
    if skin.width != 64 or skin.height not in (32, 64):
        raise ValueError(f"not a skin: {skin.width}x{skin.height}")
    skin = skin.convert("RGBA")
    face = skin.crop((8, 8, 16, 16))
    face.putalpha(255)  # the game draws the face layer opaque
    hat = skin.crop((40, 8, 48, 16))
    # Legacy 64x32 skins: a hat layer without any transparent pixel is
    # dropped (the game's "Notch transparency" rule).
    if skin.height == 32 and hat.getchannel("A").getextrema()[0] >= 128:
        hat = None
    if hat is not None:
        face = Image.alpha_composite(face, hat)
    return face.resize((SIZE, SIZE), Image.NEAREST)


def skin_url(body: bytes):
    """The SKIN url in a session-server profile, or None."""
    try:
        for prop in json.loads(body).get("properties", []):
            if prop.get("name") == "textures":
                tex = json.loads(base64.b64decode(prop["value"]))
                url = tex.get("textures", {}).get("SKIN", {}).get("url")
                return url if isinstance(url, str) and url else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return None


def head_path(out_dir, uuid):
    return os.path.join(out_dir, f"{uuid.lower()}.png")


def _remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def _write(out_dir, uuid, img):
    os.makedirs(out_dir, exist_ok=True)
    dest = head_path(out_dir, uuid)
    tmp = dest + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, dest)


def _due(conn, out_dir, now, refresh_s):
    """(uuid, stored skin url) to check, never-checked first, then oldest."""
    rows = conn.execute(
        "SELECT p.uuid, h.checked_at, h.skin_url FROM players p "
        "LEFT JOIN player_heads h ON h.uuid = p.uuid "
        "WHERE NOT EXISTS (SELECT 1 FROM blocked_players b WHERE b.uuid = p.uuid) "
        "ORDER BY h.checked_at IS NOT NULL, h.checked_at, p.uuid").fetchall()
    out = []
    for uuid, checked, url in rows:
        if is_fake_uuid(uuid):
            continue
        if (checked is None or checked <= now - refresh_s
                or (url and not os.path.exists(head_path(out_dir, uuid)))):
            out.append((uuid, url))
    return out


def _mark(conn, uuid, now, url):
    conn.execute("INSERT INTO player_heads (uuid, checked_at, skin_url) VALUES (?, ?, ?) "
                 "ON CONFLICT(uuid) DO UPDATE SET checked_at = excluded.checked_at, "
                 "skin_url = excluded.skin_url", (uuid, now, url))


def update_heads(conn, out_dir, now, fetch, settings, clock=time.monotonic) -> dict:
    """One bounded pass; commits. fetch(method, url, body) -> (status, bytes),
    status 0 = network error. settings: a Settings.heads block."""
    result = {"written": 0, "checked": 0, "removed": 0, "stopped": None}
    if not settings.enabled:
        return result
    now = int(now)
    for (uuid,) in conn.execute("SELECT uuid FROM blocked_players").fetchall():
        result["removed"] += _remove(head_path(out_dir, uuid))
    start = clock()
    calls = 0
    for uuid, old_url in _due(conn, out_dir, now, settings.refresh_hours * 3600):
        if calls >= settings.max_calls:
            result["stopped"] = "budget"
            break
        if clock() - start >= settings.max_seconds:
            result["stopped"] = "time"
            break
        calls += 1
        status, body = fetch("GET", PROFILE_URL + uuid.replace("-", ""), None)
        if status == 429:
            result["stopped"] = "rate limited"
            break
        if status == 0:
            result["stopped"] = "network error"
            break
        url = skin_url(body) if status == 200 else None
        result["checked"] += 1
        if not url:
            result["removed"] += _remove(head_path(out_dir, uuid))
            _mark(conn, uuid, now, None)
            continue
        if url == old_url and os.path.exists(head_path(out_dir, uuid)):
            _mark(conn, uuid, now, url)
            continue
        s_status, s_body = fetch("GET", url.replace("http://", "https://", 1), None)
        if s_status == 0:
            result["stopped"] = "network error"
            break
        try:
            if s_status != 200:
                raise ValueError(f"skin answered {s_status}")
            _write(out_dir, uuid, face_from_skin(s_body))
        except ValueError:
            _mark(conn, uuid, now, None)
            continue
        _mark(conn, uuid, now, url)
        result["written"] += 1
    conn.commit()
    return result
