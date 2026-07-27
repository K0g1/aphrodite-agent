"""Database schema and initialization for Aphrodite Agent."""

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- Messages (conversation history)
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at_utc);

-- Memory (short-term and long-term)
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.9,
    importance REAL NOT NULL DEFAULT 0.5,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'active',
    source_message_id TEXT,
    entities_json TEXT DEFAULT '[]',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type, status);

-- Events (world timeline)
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    layer INTEGER NOT NULL CHECK (layer BETWEEN 1 AND 3),
    provenance TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    starts_at_utc TEXT NOT NULL,
    ends_at_utc TEXT,
    local_date TEXT NOT NULL,
    location_id TEXT,
    payload_json TEXT DEFAULT '{}',
    impact_json TEXT DEFAULT '{}',
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at_utc);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(local_date);

-- Journal entries
CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    local_date TEXT NOT NULL UNIQUE,
    written_at_utc TEXT NOT NULL,
    source_event_ids_json TEXT DEFAULT '[]',
    body_text TEXT NOT NULL,
    summary_text TEXT DEFAULT '',
    mood_before_json TEXT DEFAULT '{}',
    mood_after_json TEXT DEFAULT '{}'
);

-- World state (single row)
CREATE TABLE IF NOT EXISTS world_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0,
    last_processed_utc TEXT NOT NULL,
    current_location_id TEXT NOT NULL DEFAULT 'place.home',
    current_activity TEXT NOT NULL DEFAULT 'quiet pause',
    activity_started_utc TEXT NOT NULL,
    mood_json TEXT NOT NULL DEFAULT '{}',
    weather_json TEXT NOT NULL DEFAULT '{}',
    current_setting TEXT NOT NULL DEFAULT 'home',
    updated_at_utc TEXT NOT NULL
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    character_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    source TEXT NOT NULL
);

-- Settings cache
CREATE TABLE IF NOT EXISTS settings_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
"""

SEED_WORLD_STATE = """
INSERT OR IGNORE INTO world_state (id, revision, last_processed_utc, current_location_id, current_activity, activity_started_utc, mood_json, weather_json, current_setting, updated_at_utc)
VALUES (1, 0, '', 'place.home', 'quiet pause', '', '{}', '{}', 'home', '');
"""


def get_schema_sql() -> str:
    return SCHEMA_SQL


def get_seed_sql() -> str:
    return SEED_WORLD_STATE
