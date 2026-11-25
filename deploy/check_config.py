#!/usr/bin/env python3
"""Compare the server's config.json with the repo's reference config.json.

    python3 deploy/check_config.py <server config.json> <repo config.json>

Never writes anything. Reports:
  MISSING  a key the repo's config has and the server's lacks. Outside
           stats_tracking the bot reads these directly, so it may crash.
  default  a stats_tracking key the server's config leaves out: the built-in
           default applies (api/settings.py), so this is informational.
  extra    a key only the server's config has (old or local; harmless).
  TYPE     the same key holds a different kind of value (object vs not).
  ERROR    the server's stats_tracking fails validation (load_settings).
Maps whose keys are names the owner chooses (stats_tracking.servers, ...)
are compared by entry only for the entries both sides have.

Exit 0: no MISSING/TYPE/ERROR. Exit 1: a MISSING or TYPE warning only.
Exit 2: ERROR (stats_tracking invalid; the snapshot and API would refuse it)
or an unreadable file. update.sh stops before changing anything on exit 2.
api/settings.py is imported from this file's repo (deploy/..), so the
update script can validate with the incoming code before pulling it.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Name-keyed maps: entries are the owner's choice, not a fixed key set.
NAME_MAPS = {
    "stats_tracking.servers",
    "stats_tracking.scope_groups",
    "stats_tracking.api.range_presets",
    "server.colors",
}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare(server, repo, path=""):
    """[(level, path, note)] for the differences of two config trees."""
    out = []
    for k, rv in repo.items():
        if isinstance(k, str) and k.startswith("_"):
            continue  # comment keys
        p = f"{path}.{k}" if path else k
        in_stats = p == "stats_tracking" or p.startswith("stats_tracking.")
        if k not in server:
            if in_stats:
                out.append(("default", p, "not set; the built-in default applies"))
            elif path in NAME_MAPS:
                out.append(("extra", p, "only in the repo's example (a name you may not use)"))
            else:
                out.append(("MISSING", p, f"repo example value: {_short(rv)}"))
            continue
        sv = server[k]
        if isinstance(rv, dict) != isinstance(sv, dict):
            out.append(("TYPE", p, f"repo has {_kind(rv)}, server has {_kind(sv)}"))
        elif isinstance(rv, dict):
            out.extend(compare(sv, rv, p))
    for k in server:
        if isinstance(k, str) and k.startswith("_"):
            continue
        if k not in repo:
            p = f"{path}.{k}" if path else k
            note = ("your own entry" if path in NAME_MAPS
                    else "not in the repo's config (unused, or local)")
            out.append(("extra", p, note))
    return out


def _kind(v):
    return "an object" if isinstance(v, dict) else type(v).__name__


def _short(v, n=60):
    s = json.dumps(v)
    return s if len(s) <= n else s[:n - 3] + "..."


def server_paths_warnings(cfg):
    """Each stats-tracked server needs server.<key>_path and _world_name."""
    out = []
    st = cfg.get("stats_tracking") or {}
    srv = cfg.get("server") or {}
    excluded = set(st.get("excluded_servers") or [])
    for key in (st.get("servers") or {}):
        if key.startswith("_") or key in excluded:
            continue
        for suffix in ("_path", "_world_name"):
            if f"{key}{suffix}" not in srv:
                out.append(("MISSING", f"server.{key}{suffix}",
                            f"stats_tracking.servers.{key} is tracked but has no {suffix}"))
    return out


def validate_stats(cfg):
    sys.path.insert(0, str(REPO))
    from api.settings import load_settings
    if "stats_tracking" not in cfg:
        return [("ERROR", "stats_tracking", "block missing")]
    try:
        load_settings(cfg["stats_tracking"])
    except ValueError as e:
        return [("ERROR", "stats_tracking", str(e))]
    return []


ORDER = {"ERROR": 0, "MISSING": 1, "TYPE": 2, "default": 3, "extra": 4}


def main(argv):
    if len(argv) != 2:
        print("usage: check_config.py <server config.json> <repo config.json>")
        return 2
    try:
        server, repo = _load(argv[0]), _load(argv[1])
    except (OSError, ValueError) as e:
        print(f"ERROR  can't read config: {e}")
        return 2
    found = validate_stats(server) + server_paths_warnings(server) + compare(server, repo)
    found.sort(key=lambda f: (ORDER[f[0]], f[1]))
    for level, p, note in found:
        print(f"  {level:<8}{p}: {note}")
    levels = {f[0] for f in found}
    if "ERROR" in levels:
        print("config check: ERROR (fix config.json first)")
        return 2
    if levels & {"MISSING", "TYPE"}:
        print("config check: warnings (add the MISSING keys to config.json by hand)")
        return 1
    print("config check: ok" + (" (only defaults/extras listed)" if found else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
