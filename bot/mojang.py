"""Mojang name/uuid lookups with a persistent cache and a per-run call budget.

Results (including not-found and failed lookups) are cached in `name_lookups`
and not retried for mojang.retry_after_days. Never raises on network trouble.

Time bound per run: each HTTP call times out after mojang.timeout_s, at most
mojang.max_calls calls are made, and the client stops calling once
mojang.max_seconds have passed since its first call (treated like budget
exhaustion: names not yet looked up stay uncached). Defaults: api/settings.py.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid as uuidlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.settings import DAY, DEFAULTS  # noqa: E402

BULK_URL = "https://api.minecraftservices.com/minecraft/profile/lookup/bulk/byname"
PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"
CHUNK = 10  # Mojang's bulk lookup accepts at most 10 names per request
VALID_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def default_fetch(method, url, body=None, timeout=None):
    """Return (status, body bytes); status 0 on any exception."""
    if timeout is None:
        timeout = DEFAULTS.mojang.timeout_s
    try:
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"Content-Type": "application/json", "User-Agent": "chronos-stats"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def _dash(hex_id):
    try:
        return str(uuidlib.UUID(hex_id))
    except (ValueError, AttributeError, TypeError):
        return None


class MojangClient:
    def __init__(self, fetch, conn, now, budget=None, clock=time.monotonic,
                 wall_seconds=None, settings=None):
        """settings: a settings.mojang block (DEFAULTS.mojang if None);
        budget / wall_seconds override its max_calls / max_seconds."""
        tuning = settings or DEFAULTS.mojang
        self.fetch = fetch
        self.conn = conn
        self.now = int(now)
        self.budget = tuning.max_calls if budget is None else budget
        self.calls = 0
        self.clock = clock
        self.wall_seconds = tuning.max_seconds if wall_seconds is None else wall_seconds
        self.cache_seconds = tuning.retry_after_days * DAY
        self._started = None
        self._timed_out = False

    @property
    def exhausted(self):
        """No more calls this run: budget spent or wall-clock cap reached."""
        return self.calls >= self.budget or self._timed_out

    def _spend(self):
        if self.exhausted:
            return False
        t = self.clock()
        if self._started is None:
            self._started = t
        elif t - self._started >= self.wall_seconds:
            self._timed_out = True
            return False
        self.calls += 1
        return True

    def _fresh(self, key):
        row = self.conn.execute(
            "SELECT result, checked_at FROM name_lookups WHERE lookup_key=?", (key,)).fetchone()
        if row and self.now - row[1] < self.cache_seconds:
            return row
        return None

    def _store(self, key, result):
        self.conn.execute(
            "INSERT INTO name_lookups (lookup_key, result, checked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(lookup_key) DO UPDATE SET result=excluded.result, "
            "checked_at=excluded.checked_at", (key, result, self.now))

    @staticmethod
    def _parse_hit(result):
        if not result:
            return None
        try:
            d = json.loads(result)
            return (d["uuid"], d["name"])
        except (ValueError, KeyError, TypeError):
            return None

    def names_to_uuids(self, names):
        """lowercased name -> (dashed uuid, canonical name) for names that
        resolve. Invalid usernames are skipped (not cached, no budget)."""
        out = {}
        todo = []
        seen = set()
        for n in names:
            low = n.lower()
            if not VALID_NAME.match(n) or low in seen:
                continue
            seen.add(low)
            cached = self._fresh("name:" + low)
            if cached is not None:
                hit = self._parse_hit(cached[0])
                if hit:
                    out[low] = hit
                continue
            todo.append(n)
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            if not self._spend():
                break
            status, body = self.fetch("POST", BULK_URL, json.dumps(chunk).encode())
            found = {}
            ok = status == 200
            if ok:
                try:
                    for e in json.loads(body):
                        u = _dash(e.get("id"))
                        if u and e.get("name"):
                            found[e["name"].lower()] = (u, e["name"])
                except (ValueError, AttributeError, TypeError):
                    ok = False
            for n in chunk:
                low = n.lower()
                hit = found.get(low) if ok else None
                self._store("name:" + low,
                            json.dumps({"uuid": hit[0], "name": hit[1]}) if hit else None)
                if hit:
                    out[low] = hit
            self.conn.commit()
        return out

    def uuid_to_name(self, uuid):
        uuid = _dash(uuid.replace("-", "")) or uuid.lower()
        key = "uuid:" + uuid
        cached = self._fresh(key)
        if cached is not None:
            return cached[0]
        if not self._spend():
            return None
        status, body = self.fetch("GET", PROFILE_URL + uuid.replace("-", ""), None)
        name = None
        if status == 200:
            try:
                name = json.loads(body).get("name") or None
            except (ValueError, AttributeError):
                name = None
        self._store(key, name)
        self.conn.commit()
        return name
