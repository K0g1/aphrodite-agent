"""Journal system — character writes daily reflective entries."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from ..db.database import Database
from ..config import Config
from ..types import MoodState, new_id
from ..character import Character


@dataclass
class JournalEntry:
    """A daily reflective journal entry written by the character."""
    id: str = field(default_factory=new_id)
    local_date: str = ""
    written_at_utc: str = ""
    source_event_ids: list[str] = field(default_factory=list)
    body_text: str = ""
    summary_text: str = ""
    mood_before: MoodState = field(default_factory=MoodState)
    mood_after: MoodState = field(default_factory=MoodState)


JOURNAL_WRITER_PROMPT = """You are {character_name}, writing a personal journal entry at the end of the day. Write in first person as yourself — a warm, reflective journal entry about your day. This is private, so be honest and conversational.

=== TODAY'S EVENTS ===
{events_summary}

=== MOOD BEFORE WRITING ===
{mood_description}

=== RULES ===
- Write 100-200 words
- Be reflective and conversational, not dramatic
- Don't invent events that didn't happen
- Reference 1-2 specific moments from today
- End with a brief thought about tomorrow
- Don't use emoji
- Write naturally, as if talking to a diary

=== GENERATE ENTRY ===
Write the journal entry now (first person, past tense for today's events):"""


FALLBACK_ENTRIES = [
    "Today was a pretty ordinary day. Spent most of it working on some projects, had a few quiet moments to think. Nothing特别 exciting, but sometimes those are the best days.",
    "It's been a quiet day. Got some things done, had some time to reflect. I'm feeling okay — not great, not bad. Just steady.",
    "Today felt productive. Made progress on a few things I've been putting off. Had some good conversations too. Ending the day feeling pretty content.",
]


class JournalManager:
    """Manages character journal entries."""

    def __init__(self, db: Database, config: Config, provider=None, llm=None):
        self.db = db
        self.config = config
        self._llm = llm or provider

    async def write_entry(self, character: Character, mood: MoodState,
                          world_events: list[dict] | None = None,
                          local_date: str | None = None,
                          now_utc: datetime | None = None) -> JournalEntry:
        """Write a journal entry for today."""
        now = now_utc or datetime.now(timezone.utc)
        date = local_date or now.strftime("%Y-%m-%d")

        # Check if entry already exists
        existing = await self.get_entry(date)
        if existing:
            return existing

        # Get today's events
        if world_events is None:
            world_events = await self.db.get_events_on_date(date)

        # Build events summary
        if world_events:
            events_list = []
            for evt in world_events[:8]:
                title = evt.get("title", evt.get("summary", "something happened"))
                events_list.append(f"- {title}")
            events_summary = "\n".join(events_list)
        else:
            events_summary = "- A quiet day with no notable events to report."

        # Generate entry text
        body = await self._generate_entry(character, events_summary, mood)
        if not body:
            body = random.choice(FALLBACK_ENTRIES)

        # Generate summary
        summary = self._summarize(body)

        # Create entry
        entry_id = new_id()
        event_ids = [e.get("id", "") for e in (world_events or [])[:10]]

        await self.db.save_journal(
            entry_id=entry_id,
            local_date=date,
            written_at=now.isoformat(),
            body=body,
            summary=summary,
            source_events=str(event_ids),
            mood_before=str(mood.to_dict()),
            mood_after=str(mood.to_dict()),
        )

        return JournalEntry(
            id=entry_id,
            local_date=date,
            written_at_utc=now.isoformat(),
            source_event_ids=event_ids,
            body_text=body,
            summary_text=summary,
            mood_before=mood,
            mood_after=mood,
        )

    async def _generate_entry(self, character: Character, events_summary: str,
                                mood: MoodState) -> str:
        """Generate journal entry text using LLM or fallback."""
        if not self._llm:
            return ""

        prompt = JOURNAL_WRITER_PROMPT.format(
            character_name=character.name,
            events_summary=events_summary,
            mood_description=mood.label(),
        )

        try:
            response = await self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.7,
            )
            return response.strip()
        except Exception:
            return ""

    async def get_entry(self, local_date: str) -> JournalEntry | None:
        """Get journal entry for a specific date."""
        row = await self.db.get_journal(local_date)
        if not row:
            return None
        return self._row_to_entry(row)

    async def get_latest(self) -> JournalEntry | None:
        """Get the most recent journal entry."""
        rows = await self.db.fetch_all(
            "SELECT * FROM journal_entries ORDER BY local_date DESC LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_entry(rows[0])

    async def is_due(self, now_utc: datetime, timezone_str: str = "America/Vancouver") -> bool:
        """Check if a journal entry is due."""
        try:
            from zoneinfo import ZoneInfo
            local_now = now_utc.astimezone(ZoneInfo(timezone_str))
        except Exception:
            local_now = now_utc

        local_date = local_now.strftime("%Y-%m-%d")
        current_hour = local_now.hour
        current_minute = local_now.minute

        # Parse configured journal time
        journal_time_str = self.config.world.journal_time
        journal_hour, journal_minute = map(int, journal_time_str.split(":"))

        # Is it past the configured time?
        if current_hour < journal_hour or (current_hour == journal_hour and current_minute < journal_minute):
            return False

        # Already have an entry for today?
        existing = await self.get_entry(local_date)
        return existing is None

    async def get_summary(self, days: int = 7) -> str:
        """Get a summary of recent journal entries."""
        from datetime import timedelta
        target = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = await self.db.fetch_all(
            "SELECT * FROM journal_entries WHERE local_date >= ? ORDER BY local_date DESC",
            (target,),
        )
        if not rows:
            return "No journal entries found in the last {days} days."

        parts = [f"Journal summary (last {days} days):"]
        for row in rows:
            parts.append(f"\n{row['local_date']}: {row.get('summary_text', '(no summary)')}")
        return "\n".join(parts)

    async def export_all(self) -> list[dict]:
        """Export all journal entries as a list of dicts."""
        rows = await self.db.fetch_all(
            "SELECT * FROM journal_entries ORDER BY local_date"
        )
        return [dict(r) for r in rows]

    def _summarize(self, text: str) -> str:
        """Generate a one-line summary from journal text."""
        sentences = text.replace("!", ".").replace("?", ".").split(".")
        first = sentences[0].strip() if sentences else ""
        words = first.split()
        if len(words) > 15:
            return " ".join(words[:15]) + " ..."
        return first if first else "A reflective journal entry."

    def _row_to_entry(self, row: dict) -> JournalEntry:
        import json
        try:
            mood_before = json.loads(row.get("mood_before_json", "{}"))
            mood_after = json.loads(row.get("mood_after_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            mood_before = {}
            mood_after = {}

        return JournalEntry(
            id=row["id"],
            local_date=row["local_date"],
            written_at_utc=row["written_at_utc"],
            source_event_ids=json.loads(row.get("source_event_ids_json", "[]")),
            body_text=row.get("body_text", ""),
            summary_text=row.get("summary_text", ""),
            mood_before=MoodState(**mood_before) if mood_before else MoodState(),
            mood_after=MoodState(**mood_after) if mood_after else MoodState(),
        )
