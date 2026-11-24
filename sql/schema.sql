PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
    uuid            TEXT PRIMARY KEY,
    name            TEXT,
    name_resolved   INTEGER NOT NULL DEFAULT 0,
    first_seen      INTEGER NOT NULL,
    last_activity   INTEGER,
    last_observed   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS servers (
    key               TEXT PRIMARY KEY,
    label             TEXT NOT NULL,
    group_key         TEXT NOT NULL,
    filters_bots      INTEGER NOT NULL DEFAULT 0,
    track_stats       INTEGER NOT NULL DEFAULT 1,
    track_scoreboards INTEGER NOT NULL DEFAULT 1,
    provisioned       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS player_flags (
    server_key  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    is_bot      INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (server_key, uuid)
);

CREATE TABLE IF NOT EXISTS snapshot_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            INTEGER NOT NULL,
    server_key    TEXT NOT NULL REFERENCES servers(key),
    kind          TEXT NOT NULL,
    ok            INTEGER NOT NULL,
    error         TEXT,
    entries_read  INTEGER,
    rows_written  INTEGER,
    duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_server_at ON snapshot_runs(server_key, at);

CREATE TABLE IF NOT EXISTS stat_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    server_key  TEXT NOT NULL REFERENCES servers(key),
    uuid        TEXT NOT NULL REFERENCES players(uuid),
    category    TEXT NOT NULL,
    stat_key    TEXT NOT NULL,
    value       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stat_point ON stat_snapshots(server_key, category, stat_key, uuid, at DESC);
CREATE INDEX IF NOT EXISTS idx_stat_player ON stat_snapshots(uuid, at DESC);

CREATE TABLE IF NOT EXISTS stat_current (
    server_key  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    category    TEXT NOT NULL,
    stat_key    TEXT NOT NULL,
    value       INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    seen_at     INTEGER NOT NULL,
    PRIMARY KEY (server_key, uuid, category, stat_key)
);

CREATE TABLE IF NOT EXISTS scoreboard_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    at               INTEGER NOT NULL,
    server_key       TEXT NOT NULL REFERENCES servers(key),
    participant_kind TEXT NOT NULL,
    uuid             TEXT,
    raw_name         TEXT NOT NULL,
    objective        TEXT NOT NULL,
    value            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scoreboard_point ON scoreboard_snapshots(server_key, objective, uuid, at DESC);
CREATE INDEX IF NOT EXISTS idx_scoreboard_player ON scoreboard_snapshots(uuid, at DESC) WHERE uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS scoreboard_current (
    server_key      TEXT NOT NULL,
    participant_key TEXT NOT NULL,
    objective       TEXT NOT NULL,
    value           INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    seen_at         INTEGER NOT NULL,
    PRIMARY KEY (server_key, participant_key, objective)
);

CREATE TABLE IF NOT EXISTS scoreboard_meta (
    server_key         TEXT NOT NULL,
    objective          TEXT NOT NULL,
    is_vanilla_mirror  INTEGER NOT NULL DEFAULT 0,
    first_seen         INTEGER NOT NULL,
    last_seen          INTEGER NOT NULL,
    removed            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (server_key, objective)
);

CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    server_key  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    rule_key    TEXT NOT NULL,
    category    TEXT NOT NULL,
    stat_key    TEXT,
    threshold   INTEGER NOT NULL,
    value_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_milestones_player ON milestones(uuid, at DESC);

CREATE TABLE IF NOT EXISTS milestone_rules (
    rule_key    TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    stat_key    TEXT,
    thresholds  TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS firsts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    server_key  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    category    TEXT NOT NULL,
    stat_key    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_firsts_unique ON firsts(server_key, category, stat_key);

CREATE TABLE IF NOT EXISTS badges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid        TEXT NOT NULL,
    badge_key   TEXT NOT NULL,
    awarded_at  INTEGER NOT NULL,
    server_key  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_badges_unique ON badges(uuid, badge_key, server_key);

CREATE TABLE IF NOT EXISTS dictionary_cache (
    version      TEXT PRIMARY KEY,
    fetched_at   INTEGER NOT NULL,
    content_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_milestones_unique ON milestones(server_key, uuid, category, stat_key, threshold);

CREATE TABLE IF NOT EXISTS activity (
    server_key  TEXT NOT NULL,
    at          INTEGER NOT NULL,
    uuid        TEXT NOT NULL,
    PRIMARY KEY (server_key, at, uuid)
);
CREATE INDEX IF NOT EXISTS idx_activity_uuid ON activity(uuid, at DESC);

CREATE TABLE IF NOT EXISTS stat_totals (
    server_key     TEXT NOT NULL,
    category       TEXT NOT NULL,
    stat_key       TEXT NOT NULL,
    at             INTEGER NOT NULL,
    total          INTEGER NOT NULL,
    total_no_bots  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stat_totals ON stat_totals(server_key, category, stat_key, at DESC);

CREATE TABLE IF NOT EXISTS stat_totals_current (
    server_key     TEXT NOT NULL,
    category       TEXT NOT NULL,
    stat_key       TEXT NOT NULL,
    total          INTEGER NOT NULL,
    total_no_bots  INTEGER NOT NULL,
    PRIMARY KEY (server_key, category, stat_key)
);

CREATE TABLE IF NOT EXISTS scoreboard_totals (
    server_key  TEXT NOT NULL,
    objective   TEXT NOT NULL,
    at          INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    total_no_bots INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scoreboard_totals ON scoreboard_totals(server_key, objective, at DESC);

CREATE TABLE IF NOT EXISTS scoreboard_totals_current (
    server_key  TEXT NOT NULL,
    objective   TEXT NOT NULL,
    total       INTEGER NOT NULL,
    total_no_bots INTEGER NOT NULL,
    PRIMARY KEY (server_key, objective)
);

CREATE TABLE IF NOT EXISTS rivalries (
    server_key   TEXT NOT NULL,
    category     TEXT NOT NULL,
    stat_key     TEXT NOT NULL,
    uuid_a       TEXT NOT NULL,
    uuid_b       TEXT NOT NULL,
    gap          INTEGER NOT NULL,
    gap_7d_ago   INTEGER,
    computed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS spotlight_candidates (
    trigger      TEXT NOT NULL,
    server_key   TEXT,
    uuid         TEXT,
    category     TEXT,
    stat_key     TEXT,
    value        INTEGER,
    detail_json  TEXT,
    computed_at  INTEGER NOT NULL
);

-- lookup_key is 'name:<lower>' or 'uuid:<uuid>'; result NULL = not found/failed
CREATE TABLE IF NOT EXISTS name_lookups (
    lookup_key  TEXT PRIMARY KEY,
    result      TEXT,
    checked_at  INTEGER NOT NULL
);
