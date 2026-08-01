"""Database connection and operations."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite

from .schema import get_schema_sql, get_seed_sql


WORLD_STATE_COLUMNS = frozenset(
    {
        "last_processed_utc",
        "current_location_id",
        "current_activity",
        "activity_started_utc",
        "mood_json",
        "weather_json",
        "current_setting",
        "updated_at_utc",
    }
)


class Database:
    """Async SQLite database for Aphrodite Agent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database and apply schema."""
        if self._db is not None:
            await self.close()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        # Version check FIRST so an old DB can never be silently masked by
        # CREATE TABLE IF NOT EXISTS (and a newer DB is rejected before any
        # schema statements run against it).
        await self._db.executescript(
            "CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        await self._check_schema_version()

        # Apply schema
        await self._db.executescript(get_schema_sql())
        await self._db.executescript(get_seed_sql())
        await self._db.commit()

        await self._apply_migrations()

    async def _check_schema_version(self) -> None:
        """Fail fast when the on-disk DB was created by a NEWER version."""
        try:
            row = await self.fetch_one(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            )
        except Exception:
            return
        if row is None:
            return
        try:
            disk_version = int(row["value"])
        except (TypeError, ValueError):
            return
        current = 1
        if disk_version > current:
            raise RuntimeError(
                f"Database schema version {disk_version} is newer than this build "
                f"supports ({current}); upgrade aphrodite-agent before opening it"
            )

    async def _apply_migrations(self) -> None:
        """Apply numbered .sql migrations from the migrations directory, in order."""
        import sqlite3

        from .schema import CURRENT_SCHEMA_VERSION

        db = self.conn
        # Resolve migrations in dev (repo-root/migrations) and installed
        # (site-packages/aphrodite/migrations, shipped via force-include).
        here = Path(__file__).resolve().parent.parent
        migrations_dir = here.parent.parent / "migrations"  # repo root
        if not migrations_dir.is_dir():
            migrations_dir = here / "migrations"  # packaged install
        if not migrations_dir.is_dir():
            return
        applied_row = await self.fetch_one(
            "SELECT value FROM schema_metadata WHERE key = 'migrations_applied'"
        )
        applied = set()
        if applied_row and applied_row.get("value"):
            applied = {s.strip() for s in str(applied_row["value"]).split(",") if s.strip()}

        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            script = path.read_text(encoding="utf-8")
            try:
                await db.executescript(script)
                await db.commit()
            except sqlite3.Error as exc:
                await db.rollback()
                raise RuntimeError(f"Migration {path.name} failed: {exc}") from exc
            applied.add(path.name)
            await self.execute(
                "INSERT OR REPLACE INTO schema_metadata (key, value) VALUES ('migrations_applied', ?)",
                (",".join(sorted(applied)),),
            )
            await db.commit()

        if CURRENT_SCHEMA_VERSION > 1:
            await self.execute(
                "UPDATE schema_metadata SET value = ? WHERE key = 'schema_version'",
                (str(CURRENT_SCHEMA_VERSION),),
            )
            await db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not initialized")
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute with bounded retry on transient SQLITE_BUSY/LOCKED errors."""
        conn = self.conn
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(4):
            try:
                return await conn.execute(sql, params)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.05 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise sqlite3.OperationalError("database is locked after retries")

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
    async def save_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        token_count: int = 0,
        created_at: str = "",
        metadata: str = "{}",
    ) -> None:
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
    async def save_memory(
        self,
        memory_id: str,
        memory_type: str,
        content: str,
        confidence: float = 0.9,
        importance: float = 0.5,
        sensitivity: str = "normal",
        created_at: str = "",
        source_message_id: str = "",
    ) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO memories (id, memory_type, content, confidence, importance, sensitivity, source_message_id, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                memory_id,
                memory_type,
                content,
                confidence,
                importance,
                sensitivity,
                source_message_id or None,
                created_at,
                created_at,
            ),
        )
        await self.commit()

    async def search_memories(self, query: str, limit: int = 8) -> list[dict]:
        """Search memories with LIKE, escaping user wildcards so %/_ match literally."""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        return await self.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' AND (content LIKE ? ESCAPE '\\' OR entities_json LIKE ? ESCAPE '\\') ORDER BY importance DESC, created_at_utc DESC LIMIT ?",
            (pattern, pattern, limit),
        )

    async def get_short_term_memories(self, limit: int = 30) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' ORDER BY created_at_utc DESC LIMIT ?",
            (limit,),
        )

    # --- Event operations ---
    async def save_event(
        self,
        event_id: str,
        event_type: str,
        layer: int,
        provenance: str,
        status: str,
        title: str,
        summary: str,
        starts_at: str,
        local_date: str,
        location_id: str = "",
        payload: str = "{}",
        impact: str = "{}",
        created_at: str | None = None,
    ) -> None:
        from datetime import datetime, timezone

        now = created_at or datetime.now(timezone.utc).isoformat()
        await self.execute(
            "INSERT OR REPLACE INTO events (id, event_type, layer, provenance, status, title, summary, starts_at_utc, local_date, location_id, payload_json, impact_json, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event_type,
                layer,
                provenance,
                status,
                title,
                summary,
                starts_at,
                local_date,
                location_id,
                payload,
                impact,
                now,
            ),
        )
        await self.commit()

    async def get_events_on_date(self, local_date: str) -> list[dict]:
        return await self.fetch_all(
            "SELECT * FROM events WHERE local_date = ? ORDER BY starts_at_utc",
            (local_date,),
        )

    # --- Journal operations ---
    async def save_journal(
        self,
        entry_id: str,
        local_date: str,
        written_at: str,
        body: str,
        summary: str = "",
        source_events: str = "[]",
        mood_before: str = "{}",
        mood_after: str = "{}",
    ) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO journal_entries (id, local_date, written_at_utc, source_event_ids_json, body_text, summary_text, mood_before_json, mood_after_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                local_date,
                written_at,
                source_events,
                body,
                summary,
                mood_before,
                mood_after,
            ),
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
        unknown_columns = set(kwargs) - WORLD_STATE_COLUMNS
        if unknown_columns:
            names = ", ".join(sorted(unknown_columns))
            raise ValueError(f"Unknown world-state column(s): {names}")
        if kwargs:
            # Single UPDATE statement: atomic by construction (no partial state).
            assignments = ", ".join(f"{column} = ?" for column in kwargs)
            params = tuple(kwargs[column] for column in kwargs)
            # Column names come exclusively from WORLD_STATE_COLUMNS, whose
            # membership is validated above; no user input reaches the identifier.
            await self.execute(
                f"UPDATE world_state SET {assignments}, revision = revision + 1 WHERE id = 1",  # nosec B608
                params,
            )
            await self.commit()
