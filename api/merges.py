"""Account merges (player_merges.json at the repo root): separate Minecraft
accounts whose stats count as one player. Pure stdlib; read by the snapshot,
derive's merge step, the backup import and the API.

Entry: {"from": uuid, "to": uuid, "names": [from-account scoreboard names]}.
Chains (a -> b, b -> c) resolve to the final target. A uuid can't be both
merged away and merged into from itself.

`renames`: {"uuid": uuid, "names": [former names], "from": "YYYY-MM-DD",
"until": "YYYY-MM-DD"} (dates optional, UTC, both inclusive): scoreboard
entries under a former name of ONE account (scoreboards key by name; a name
in no usercache can't be tied to its uuid otherwise). The dates bound when
the name was this uuid's, for a name later used by someone else."""
import calendar
import json
import time
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path

MERGES_PATH = Path(__file__).resolve().parent.parent / "player_merges.json"


@dataclass(frozen=True)
class Rename:
    uuid: str
    name: str            # lowercased
    display: str         # as written in the file
    from_ts: int = None  # None = unbounded
    until_ts: int = None

    def covers(self, at):
        return at is None or ((self.from_ts is None or at >= self.from_ts)
                              and (self.until_ts is None or at <= self.until_ts))


@dataclass(frozen=True)
class Merges:
    to_of: dict = field(default_factory=dict)      # from uuid -> final target uuid
    by_name: dict = field(default_factory=dict)    # lowercased from-name -> final target
    names_of: dict = field(default_factory=dict)   # from uuid -> {lowercased names}
    renames: tuple = ()                            # Rename entries
    display: dict = field(default_factory=dict)    # lowercased merge name -> as written

    def target(self, uuid):
        return self.to_of.get(uuid, uuid)

    def rename_target(self, name, at=None):
        """The uuid a former name belonged to at `at` (any time if None), or
        None. Merged-away uuids resolve to their target."""
        n = (name or "").lower()
        for r in self.renames:
            if r.name == n and r.covers(at):
                return self.target(r.uuid)
        return None

    def merged_from(self, uuid):
        """Accounts merged into uuid, sorted."""
        return sorted(a for a, b in self.to_of.items() if b == uuid)

    def former_names(self, uuid):
        """Names (as written) of uuid's renames and of the accounts merged
        into it, deduplicated case-insensitively, sorted."""
        out = {}
        for r in self.renames:
            if self.target(r.uuid) == uuid:
                out.setdefault(r.name, r.display)
        for a in self.merged_from(uuid):
            for n in self.names_of.get(a, ()):
                out.setdefault(n, self.display.get(n, n))
        return sorted(out.values(), key=lambda n: (n.lower(), n))

    def __bool__(self):
        return bool(self.to_of) or bool(self.renames)


def _uuid(v, where):
    try:
        return str(uuidlib.UUID(str(v)))
    except ValueError:
        raise ValueError(f"player_merges.json {where}: {v!r} is not a uuid")


def _date(v, where, end=False):
    if v is None:
        return None
    try:
        t = calendar.timegm(time.strptime(str(v), "%Y-%m-%d"))
    except ValueError:
        raise ValueError(f"player_merges.json {where}: {v!r} is not a YYYY-MM-DD date")
    return t + 86399 if end else t


def _parse_renames(entries, merge_names):
    if not isinstance(entries, list):
        raise ValueError("player_merges.json: 'renames' must be a list")
    out = []
    for i, e in enumerate(entries):
        where = f"renames[{i}]"
        if not isinstance(e, dict) or "uuid" not in e:
            raise ValueError(f"player_merges.json {where}: needs 'uuid' and 'names'")
        u = _uuid(e["uuid"], f"{where}.uuid")
        names = e.get("names")
        if not isinstance(names, list) or not names or not all(
                isinstance(n, str) and n for n in names):
            raise ValueError(f"player_merges.json {where}.names must be a non-empty list of names")
        lo, hi = _date(e.get("from"), f"{where}.from"), _date(e.get("until"), f"{where}.until", True)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"player_merges.json {where}: from is after until")
        for n in names:
            if n.lower() in merge_names:
                raise ValueError(f"player_merges.json {where}: {n!r} is also a merge name")
            r = Rename(u, n.lower(), n, lo, hi)
            for o in out:
                if o.name == r.name and o.uuid != u and _overlap(o, r):
                    raise ValueError(f"player_merges.json {where}: {n!r} belongs to two "
                                     "uuids at the same time (use from/until)")
            out.append(r)
    return tuple(out)


def _overlap(a, b):
    lo = max(x for x in (a.from_ts, b.from_ts, -1) if x is not None)
    hi = min(x for x in (a.until_ts, b.until_ts, 2 ** 62) if x is not None)
    return lo <= hi


def parse_merges(raw) -> Merges:
    entries = raw.get("merges", []) if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError("player_merges.json: 'merges' must be a list")
    direct, names_of, display = {}, {}, {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "from" not in e or "to" not in e:
            raise ValueError(f"player_merges.json merges[{i}]: needs 'from' and 'to'")
        a, b = _uuid(e["from"], f"merges[{i}].from"), _uuid(e["to"], f"merges[{i}].to")
        if a == b:
            raise ValueError(f"player_merges.json merges[{i}]: from == to")
        if a in direct and direct[a] != b:
            raise ValueError(f"player_merges.json merges[{i}]: {a} is merged twice")
        direct[a] = b
        names = e.get("names", [])
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise ValueError(f"player_merges.json merges[{i}].names must be a list of names")
        names_of.setdefault(a, set()).update(n.lower() for n in names)
        for n in names:
            display.setdefault(n.lower(), n)
    to_of = {}
    for a in direct:
        seen, t = {a}, direct[a]
        while t in direct:
            if t in seen:
                raise ValueError(f"player_merges.json: merge cycle through {t}")
            seen.add(t)
            t = direct[t]
        to_of[a] = t
    by_name = {n: to_of[a] for a, ns in names_of.items() for n in ns}
    renames = _parse_renames(raw.get("renames", []), set(by_name))
    return Merges(to_of=to_of, by_name=by_name, names_of=names_of, renames=renames,
                  display=display)


_api_merges = None


def api_merges() -> Merges:
    """The API's copy, read once per process (restart to apply)."""
    global _api_merges
    if _api_merges is None:
        _api_merges = load_merges()
    return _api_merges


def reset_api_merges():
    global _api_merges
    _api_merges = None


def load_merges(path=None) -> Merges:
    """Missing file = no merges. A bad file raises (never silently ignored)."""
    p = Path(path) if path else MERGES_PATH
    if not p.is_file():
        return Merges()
    with open(p, "r", encoding="utf-8") as f:
        return parse_merges(json.load(f))
