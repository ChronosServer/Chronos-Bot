"""Account merges (player_merges.json at the repo root): separate Minecraft
accounts whose stats count as one player. Pure stdlib; read by the snapshot,
derive's merge step, the backup import and the API.

Entry: {"from": uuid, "to": uuid, "names": [from-account scoreboard names]}.
Chains (a -> b, b -> c) resolve to the final target. A uuid can't be both
merged away and merged into from itself."""
import json
import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path

MERGES_PATH = Path(__file__).resolve().parent.parent / "player_merges.json"


@dataclass(frozen=True)
class Merges:
    to_of: dict = field(default_factory=dict)      # from uuid -> final target uuid
    by_name: dict = field(default_factory=dict)    # lowercased from-name -> final target
    names_of: dict = field(default_factory=dict)   # from uuid -> {lowercased names}

    def target(self, uuid):
        return self.to_of.get(uuid, uuid)

    def __bool__(self):
        return bool(self.to_of)


def _uuid(v, where):
    try:
        return str(uuidlib.UUID(str(v)))
    except ValueError:
        raise ValueError(f"player_merges.json {where}: {v!r} is not a uuid")


def parse_merges(raw) -> Merges:
    entries = raw.get("merges", []) if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError("player_merges.json: 'merges' must be a list")
    direct, names_of = {}, {}
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
    return Merges(to_of=to_of, by_name=by_name, names_of=names_of)


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
