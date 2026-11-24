"""Read-only SQLite access for the API."""
import json
import os
import sqlite3
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


def db_path() -> str:
    env = os.environ.get("CHRONOS_STATS_DB")
    if env:
        return env
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["stats_tracking"]["db_path"]


def get_conn() -> sqlite3.Connection:
    uri = Path(db_path()).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA cache_size=-65536")
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
