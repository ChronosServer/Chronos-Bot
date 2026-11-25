"""Which player names does name_blocklist.txt catch? Read-only.

    python3 tools/check_blocklist.py [--db PATH]

Prints every match among the database's player names, former names
(player_merges.json) and scoreboard names, with the term that hit, and the
allowlisted ones separately. Add innocent names (or uuids) to
name_allowlist.txt. Default DB: stats_tracking.db_path in config.json."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

from api import names  # noqa: E402
from api.merges import known_former_names, load_merges  # noqa: E402


def matches(conn, merges, terms, allow):
    """[(name, uuid or '', term, allowlisted)] sorted by name."""
    cands = {}
    for uuid, name in conn.execute("SELECT uuid, name FROM players"):
        for n in ([name] if name else []) + known_former_names(conn, merges, uuid):
            cands.setdefault((n, uuid), None)
    for (raw,) in conn.execute("SELECT DISTINCT raw_name FROM scoreboard_snapshots "
                               "WHERE uuid IS NULL"):
        cands.setdefault((raw, ""), None)
    out = []
    for n, uuid in cands:
        term = names.match(n, terms)
        if term:
            out.append((n, uuid, term, n.lower() in allow or uuid.lower() in allow))
    return sorted(out, key=lambda r: (r[0].lower(), r[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="stats database (default: config.json db_path)")
    args = ap.parse_args(argv)
    db = args.db or json.load(open(ROOT / "config.json", encoding="utf-8"))[
        "stats_tracking"]["db_path"]
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = matches(conn, load_merges(), names.load_terms(), names.load_allow())
    blocked = [r for r in rows if not r[3]]
    allowed = [r for r in rows if r[3]]
    print(f"blocked ({len(blocked)}):")
    for n, uuid, term, _ in blocked:
        print(f"  {n:<24} {uuid or '(scoreboard name)':<38} {term}")
    print(f"allowlisted ({len(allowed)}):")
    for n, uuid, term, _ in allowed:
        print(f"  {n:<24} {uuid or '(scoreboard name)':<38} {term}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
