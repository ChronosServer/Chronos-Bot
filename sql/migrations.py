"""Versioned SQLite migrations, tracked via PRAGMA user_version.

schema.sql holds the CREATE IF NOT EXISTS baseline; migrations here apply
column additions to tables that may already exist in a live DB.
"""
import sqlite3


def _has_column(conn, table, column):
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn, table, column, ddl):
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _v1(conn):
    _add_column(conn, "firsts", "earliest_known", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "milestones", "earliest_known", "INTEGER NOT NULL DEFAULT 0")


def _v2(conn):
    # Serves /milestones' ORDER BY. Lives here, not in schema.sql: schema.sql
    # runs before migrations, and earliest_known is added by _v1.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_milestones_feed "
                 "ON milestones(earliest_known, at DESC, id DESC)")


MIGRATIONS = [_v1, _v2]


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, fn in enumerate(MIGRATIONS, start=1):
        if i > version:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {i}")
    conn.commit()
