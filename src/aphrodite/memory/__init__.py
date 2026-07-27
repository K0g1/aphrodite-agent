"""Memory system — short-term and long-term memory management."""

from __future__ import annotations

from ..db.database import Database
from ..types import Memory, MemoryType, Sensitivity, new_id
from ..config import Config


class MemoryManager:
    """Manages short-term and long-term memory."""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    async def add_memory(
        self,
        content: str,
        memory_type: str = "fact",
        confidence: float = 0.9,
        importance: float = 0.5,
        sensitivity: str = "normal",
        source_message_id: str = "",
    ) -> Memory:
        """Add a new memory."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        memory_id = new_id()

        await self.db.save_memory(
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            created_at=now,
        )

        return Memory(
            id=memory_id,
            memory_type=MemoryType(memory_type),
            content=content,
            confidence=confidence,
            importance=importance,
            sensitivity=Sensitivity(sensitivity),
            source_message_id=source_message_id,
            created_at=now,
            updated_at=now,
        )

    async def get_short_term(self, limit: int | None = None) -> list[Memory]:
        """Get short-term memories (most recent, always injected)."""
        limit = limit or self.config.short_term_max_entries
        rows = await self.db.get_short_term_memories(limit)
        return [self._row_to_memory(r) for r in rows]

    async def search_long_term(self, query: str, limit: int | None = None) -> list[Memory]:
        """Search long-term memories by relevance."""
        limit = limit or self.config.long_term_max_results
        rows = await self.db.search_memories(query, limit)
        return [self._row_to_memory(r) for r in rows]

    async def correct_memory(self, memory_id: str, new_content: str) -> None:
        """Correct a memory (mark old as superseded, create new)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        # Get old memory
        old = await self.db.fetch_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if old:
            # Mark old as superseded
            await self.db.execute(
                "UPDATE memories SET status = 'superseded', updated_at_utc = ? WHERE id = ?",
                (now, memory_id),
            )
            # Create new memory
            await self.db.save_memory(
                memory_id=new_id(),
                memory_type=old["memory_type"],
                content=new_content,
                confidence=1.0,  # Direct correction = highest confidence
                importance=old.get("importance", 0.5),
                sensitivity=old.get("sensitivity", "normal"),
                created_at=now,
            )
            await self.db.commit()

    async def forget_memory(self, memory_id: str) -> None:
        """Delete a memory."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE memories SET status = 'deleted', updated_at_utc = ? WHERE id = ?",
            (now, memory_id),
        )
        await self.db.commit()

    async def get_stats(self) -> dict:
        """Get memory statistics."""
        total = await self.db.fetch_one("SELECT COUNT(*) as c FROM memories WHERE status = 'active'")
        by_type = await self.db.fetch_all(
            "SELECT memory_type, COUNT(*) as c FROM memories WHERE status = 'active' GROUP BY memory_type"
        )
        return {
            "total_active": total["c"] if total else 0,
            "by_type": {r["memory_type"]: r["c"] for r in by_type},
        }

    def _row_to_memory(self, row: dict) -> Memory:
        return Memory(
            id=row["id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            confidence=row.get("confidence", 0.9),
            importance=row.get("importance", 0.5),
            sensitivity=Sensitivity(row.get("sensitivity", "normal")),
            source_message_id=row.get("source_message_id", ""),
            status=row.get("status", "active"),
            created_at=row.get("created_at_utc", ""),
            updated_at=row.get("updated_at_utc", ""),
        )
