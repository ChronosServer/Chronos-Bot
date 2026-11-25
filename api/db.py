"""Read-only SQLite access for the API."""
import json
import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from api.settings import api_settings

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@lru_cache(maxsize=1)
def config_tracking():
    """config.json's stats_tracking block, read once; None with no config
    file. Source of both db_path and the API's settings (api/settings.py)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("stats_tracking")
    except FileNotFoundError:
        return None


def _config_db_path() -> str:
    tracking = config_tracking()
    if not tracking or not tracking.get("db_path"):
        raise RuntimeError(f"no stats_tracking.db_path in {CONFIG_PATH} "
                           "and CHRONOS_STATS_DB is not set")
    return tracking["db_path"]


def db_path() -> str:
    # Env var is checked live each call; config.json is read once.
    return os.environ.get("CHRONOS_STATS_DB") or _config_db_path()


def get_conn() -> sqlite3.Connection:
    uri = Path(db_path()).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    api = api_settings().api
    conn.execute(f"PRAGMA mmap_size={int(api.sqlite_mmap_bytes)}")
    conn.execute(f"PRAGMA cache_size=-{int(api.sqlite_cache_kib)}")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def get_db():
    """FastAPI dependency: one read-only connection per request."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def data_version(conn) -> str:
    """Latest snapshot_runs id, read live ("0" if none). Deliberately not
    meta.data_version, so a failed derive cannot pin caches to stale data."""
    row = conn.execute("SELECT MAX(id) FROM snapshot_runs").fetchone()
    return str(row[0] if row and row[0] is not None else 0)


# Indexes the API pins with INDEXED BY; created by bot/snapshot.py (schema.sql
# + sql/migrations.py; the API is read-only).
REQUIRED_INDEXES = ("idx_stat_point", "idx_scoreboard_point",
                    "idx_stat_current_key", "idx_scoreboard_current_obj",
                    "idx_milestones_feed")


def check_required_indexes() -> None:
    conn = get_conn()
    try:
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    missing = [i for i in REQUIRED_INDEXES if i not in have]
    if missing:
        raise RuntimeError("run bot/snapshot.py once to apply the schema; "
                           f"missing indexes: {', '.join(missing)}")
