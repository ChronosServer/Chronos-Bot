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
import http.client
import io
import json
import os
import socket
import time
import urllib.error
import urllib.request

from PIL import Image, UnidentifiedImageError

from api.scope import is_fake_uuid

PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"
SIZE = 64


def _ipv4_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                            source_address=None, *args, **kwargs):
    """socket.create_connection over IPv4 addresses only."""
    host, port = address
    err = None
    for af, st, proto, _, addr in socket.getaddrinfo(host, port, socket.AF_INET,
                                                     socket.SOCK_STREAM):
        sock = socket.socket(af, st, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            return sock
        except OSError as e:
            err = e
            sock.close()
    raise err or OSError(f"no IPv4 address for {host}")


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _ipv4_create_connection


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


def ipv4_fetch(timeout):
    """fetch(method, url, body) -> (status, bytes) over IPv4 only, status 0 on
    any error (like mojang.default_fetch). Mojang's session server rate-limits
    the live server's IPv6 address (shared with the Minecraft servers' skin
    lookups) on every request, not its IPv4 (stats_tracking.heads.ipv4_only)."""
    opener = urllib.request.build_opener(_IPv4HTTPSHandler())

    def fetch(method, url, body=None):
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"User-Agent": "chronos-stats"})
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            return 0, b""
    return fetch


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
    """The SKIN url in a session-server profile; None = the profile has no
    skin. ValueError if the body is not a profile (an error page)."""
    try:
        profile = json.loads(body)
        props = profile["properties"]
        for prop in props:
            if prop.get("name") == "textures":
                tex = json.loads(base64.b64decode(prop["value"]))
                url = tex.get("textures", {}).get("SKIN", {}).get("url")
                return url if isinstance(url, str) and url else None
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise ValueError(f"not a profile: {e}") from None
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


def _due(conn, out_dir, now, refresh_s, skip=()):
    """(uuid, stored skin url) to check, never-checked first, then oldest."""
    rows = conn.execute(
        "SELECT p.uuid, h.checked_at, h.skin_url FROM players p "
        "LEFT JOIN player_heads h ON h.uuid = p.uuid "
        "WHERE NOT EXISTS (SELECT 1 FROM blocked_players b WHERE b.uuid = p.uuid) "
        "ORDER BY h.checked_at IS NOT NULL, h.checked_at, p.uuid").fetchall()
    out = []
    for uuid, checked, url in rows:
        if is_fake_uuid(uuid) or uuid in skip:
            continue
        if (checked is None or checked <= now - refresh_s
                or (url and not os.path.exists(head_path(out_dir, uuid)))):
            out.append((uuid, url))
    return out


def _mark(conn, uuid, now, url):
    """Record a check; committed at once, so no write lock is held across
    the next HTTP calls."""
    conn.execute("INSERT INTO player_heads (uuid, checked_at, skin_url) VALUES (?, ?, ?) "
                 "ON CONFLICT(uuid) DO UPDATE SET checked_at = excluded.checked_at, "
                 "skin_url = excluded.skin_url", (uuid, now, url))
    conn.commit()


def update_heads(conn, out_dir, now, fetch, settings, clock=time.monotonic, skip=()) -> dict:
    """One bounded pass. fetch(method, url, body) -> (status, bytes), status
    0 = network error. settings: a Settings.heads block. skip: uuids that get
    no head (accounts merged into another). Only a real answer changes
    anything: 204/404 = no such profile, a profile without a skin = no skin;
    any other status (5xx, 403, 429, an error page) ends the step and leaves
    the player's file and row as they were."""
    result = {"written": 0, "checked": 0, "removed": 0, "stopped": None}
    if not settings.enabled:
        return result
    now = int(now)
    gone = [u for (u,) in conn.execute("SELECT uuid FROM blocked_players").fetchall()]
    for uuid in gone + sorted(skip):
        result["removed"] += _remove(head_path(out_dir, uuid))
    start = clock()
    calls = 0
    for uuid, old_url in _due(conn, out_dir, now, settings.refresh_hours * 3600, skip):
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
        if status in (204, 404):
            url = None
        elif status == 200:
            try:
                url = skin_url(body)
            except ValueError:
                result["stopped"] = "bad profile answer"
                break
        else:
            result["stopped"] = f"session server answered {status}"
            break
        result["checked"] += 1
        if not url:
            result["removed"] += _remove(head_path(out_dir, uuid))
            _mark(conn, uuid, now, None)
            continue
        if url == old_url and os.path.exists(head_path(out_dir, uuid)):
            _mark(conn, uuid, now, url)
            continue
        if clock() - start >= settings.max_seconds:
            result["stopped"] = "time"
            break
        s_status, s_body = fetch("GET", url.replace("http://", "https://", 1), None)
        if s_status == 0:
            result["stopped"] = "network error"
            break
        if s_status not in (200, 404, 410):
            result["stopped"] = f"skin server answered {s_status}"
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
