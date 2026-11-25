"""Slur filter on player names (name_blocklist.txt / name_allowlist.txt at the
repo root, owner-edited). Pure stdlib; derive (bot/derive_blocked.py) and
tools/check_blocklist.py use it.

A name is blocked when a normalised term is a substring of the normalised
name. Normalising: lowercase, drop '_', map leetspeak (0->o 1->i 3->e 4->a
5->s 7->t @->a $->s), then collapse runs of one letter ("nigger" -> "niger",
so doubled or tripled letters still match)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKLIST_PATH = ROOT / "name_blocklist.txt"
ALLOWLIST_PATH = ROOT / "name_allowlist.txt"

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
                       "@": "a", "$": "s"})


def normalize(s: str) -> str:
    s = (s or "").lower().replace("_", "").translate(_LEET)
    out = []
    for ch in s:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _lines(path):
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def load_terms(path=None) -> list:
    """Normalised terms, deduplicated, in file order. Missing file = []."""
    seen = []
    for t in _lines(path or BLOCKLIST_PATH):
        n = normalize(t)
        if n and n not in seen:
            seen.append(n)
    return seen


def load_allow(path=None) -> set:
    """Exempt names and uuids, lowercased. Missing file = empty."""
    return {t.lower() for t in _lines(path or ALLOWLIST_PATH)}


def match(name, terms):
    """The first term found in the normalised name, or None."""
    if not name:
        return None
    n = normalize(name)
    for t in terms:
        if t in n:
            return t
    return None
