"""Database connection and operations."""

from __future__ import annotations

import aiosqlite
from pathlib import Path

from .schema import get_schema_sql, get_seed_sql


class Database:
    """Async SQLite database for Aphrodite Agent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database and apply schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        
        # Apply schema
        await self._db.executescript(get_schema_sql())
        await self._db.executescript(get_seed_sql())
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not initialized"
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def commit(self) -> None:
        await self.conn.commit()

    # --- Message operations ---
    async def save_message(self, message_id: str, conversation_id: str,
                           role: str, content: str, token_count: int = 0,
                           created_at: str = "", metadata: str = "{}") -> None:
        await self.execute(
            "INSERT OR REPLACE INTO messages (id, conversation_id, role, content, token_count, created_at_utc, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, conversation_id, role, content, token_count, created_at, metadata),
        )
        await self.commit()

    async def get_recent_messages(self, conversation_id: str, limit: int = 10) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at_utc DESC LIMIT ?",
            (conversation_id, limit),
        )

    # --- Memory operations ---
    async def save_memory(self, memory_id: str, memory_type: str, content: str,
                          confidence: float = 0.9, importance: float = 0.5,
                          sensitivity: str = "normal", created_at: str = "") -> None:
        await self.execute(
            "INSERT OR REPLACE INTO memories (id, memory_type, content, confidence, importance, sensitivity, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (memory_id, memory_type, content, confidence, importance, sensitivity, created_at, created_at),
        )
        await self.commit()

    async def search_memories(self, query: str, limit: int = 8) -> list[dict]:
        """Simple FTS-like search using LIKE."""
        pattern = f"%{query}%"
        return await self.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' AND (content LIKE ? OR entities_json LIKE ?) ORDER BY importance DESC, created_at_utc DESC LIMIT ?",
            (pattern, pattern, limit),
        )

    async def get_short_term_memories(self, limit: int = 30) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' ORDER BY created_at_utc DESC LIMIT ?",
            (limit,),
        )

    # --- Event operations ---
    async def save_event(self, event_id: str, event_type: str, layer: int,
                         provenance: str, status: str, title: str, summary: str,
                         starts_at: str, local_date: str, location_id: str = "",
                         payload: str = "{}", impact: str = "{}") -> None:
        await self.execute(
            "INSERT OR REPLACE INTO events (id, event_type, layer, provenance, status, title, summary, starts_at_utc, local_date, location_id, payload_json, impact_json, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, layer, provenance, status, title, summary, starts_at, local_date, location_id, payload, impact, starts_at),
        )
        await self.commit()

    async def get_events_on_date(self, local_date: str) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM events WHERE local_date = ? ORDER BY starts_at_utc",
            (local_date,),
        )

    # --- Journal operations ---
    async def save_journal(self, entry_id: str, local_date: str, written_at: str,
                           body: str, summary: str = "", source_events: str = "[]",
                           mood_before: str = "{}", mood_after: str = "{}") -> None:
        await self.execute(
            "INSERT OR REPLACE INTO journal_entries (id, local_date, written_at_utc, source_event_ids_json, body_text, summary_text, mood_before_json, mood_after_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, local_date, written_at, source_events, body, summary, mood_before, mood_after),
        )
        await self.commit()

    async def get_journal(self, local_date: str) -> dict | None:
        return await self.fetch_one(
            "SELECT * FROM journal_entries WHERE local_date = ?",
            (local_date,),
        )

    # --- World state ---
    async def get_world_state(self) -> dict | None:
        return await self.fetch_one("SELECT * FROM world_state WHERE id = 1")

    async def update_world_state(self, **kwargs) -> None:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if sets:
            sets.append("revision = revision + 1")
            await self.execute(
                f"UPDATE world_state SET {', '.join(sets)} WHERE id = 1",
                tuple(vals),
            )
            await self.commit()
