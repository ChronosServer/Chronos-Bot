"""derive stage: blocked_players = players whose name (or a former name from
player_merges.json) matches name_blocklist.txt, minus name_allowlist.txt.
Rewritten every run, so a list edit or a rename applies at the next
snapshot. The API hides these uuids everywhere (api.scope)."""
from api import names
from api.merges import known_former_names


def update_blocked(conn, merges=None) -> int:
    """Rewrites blocked_players; returns the number blocked. No commit."""
    terms = names.load_terms()
    allow = names.load_allow()
    rows = []
    if terms:
        for uuid, name in conn.execute("SELECT uuid, name FROM players").fetchall():
            if uuid.lower() in allow:
                continue
            cands = ([name] if name else []) + known_former_names(conn, merges, uuid)
            for n in cands:
                if n.lower() in allow:
                    continue
                term = names.match(n, terms)
                if term:
                    rows.append((uuid, n, term))
                    break
    conn.execute("DELETE FROM blocked_players")
    conn.executemany("INSERT INTO blocked_players (uuid, name, term) VALUES (?, ?, ?)", rows)
    return len(rows)
