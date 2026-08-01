"""Journal system — character writes daily reflective entries."""

from __future__ import annotations

import ast
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from ..db.database import Database
from ..config import Config
from ..types import MoodState, new_id
from ..character import Character


logger = logging.getLogger("aphrodite.journal")


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
    "Today was a pretty ordinary day. Spent most of it working on some projects, had a few quiet moments to think. Nothing especially exciting, but sometimes those are the best days.",
    "It's been a quiet day. Got some things done, had some time to reflect. I'm feeling okay — not great, not bad. Just steady.",
    "Today felt productive. Made progress on a few things I've been putting off. Had some good conversations too. Ending the day feeling pretty content.",
]


class JournalManager:
    """Manages character journal entries."""

    def __init__(self, db: Database, config: Config, provider=None, llm=None):
        self.db = db
        self.config = config
        self._llm = llm or provider

    async def write_entry(
        self,
        character: Character,
        mood: MoodState,
        world_events: list[dict] | None = None,
        local_date: str | None = None,
        now_utc: datetime | None = None,
    ) -> JournalEntry:
        """Write a journal entry for today."""
        now = now_utc or datetime.now(timezone.utc)
        # Derive the date in the configured local timezone, never UTC, so
        # entries land on the day the character actually experienced.
        date: str = local_date or self._to_local_time(now).strftime("%Y-%m-%d")

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
            body = secrets.choice(FALLBACK_ENTRIES)

        # Generate summary
        summary = self._summarize(body)

        # Create entry
        entry_id = new_id()
        event_ids = [e.get("id", "") for e in (world_events or [])[:10]]

        # A small reflective delta for the after-writing mood (slightly calmer
        # than before) so the two fields are not always identical.
        mood_after = MoodState(
            valence=max(-1.0, min(1.0, mood.valence + 0.02)),
            arousal=max(0.0, min(1.0, mood.arousal - 0.03)),
            dominance=mood.dominance,
            affection=mood.affection,
            trust=mood.trust,
            curiosity=mood.curiosity,
        )

        await self.db.save_journal(
            entry_id=entry_id,
            local_date=date,
            written_at=now.isoformat(),
            body=body,
            summary=summary,
            source_events=json.dumps(event_ids),
            mood_before=json.dumps(mood.to_dict()),
            mood_after=json.dumps(mood_after.to_dict()),
        )

        return JournalEntry(
            id=entry_id,
            local_date=date,
            written_at_utc=now.isoformat(),
            source_event_ids=event_ids,
            body_text=body,
            summary_text=summary,
            mood_before=mood,
            mood_after=mood_after,
        )

    async def _generate_entry(
        self, character: Character, events_summary: str, mood: MoodState
    ) -> str:
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

    def _to_local_time(self, now_utc: datetime, timezone_str: str | None = None) -> datetime:
        """Convert an aware UTC timestamp to the configured local timezone."""
        timezone_name = timezone_str or self.config.timezone
        if timezone_name == "system":
            return now_utc.astimezone()
        from zoneinfo import ZoneInfo

        return now_utc.astimezone(ZoneInfo(timezone_name))

    async def is_due(self, now_utc: datetime, timezone_str: str | None = None) -> bool:
        """Check if a journal entry is due in the configured timezone."""
        local_now = self._to_local_time(now_utc, timezone_str)

        local_date = local_now.strftime("%Y-%m-%d")
        current_hour = local_now.hour
        current_minute = local_now.minute

        # Parse configured journal time
        journal_time_str = self.config.world.journal_time
        journal_hour, journal_minute = map(int, journal_time_str.split(":"))

        # Is it past the configured time?
        if current_hour < journal_hour or (
            current_hour == journal_hour and current_minute < journal_minute
        ):
            return False

        # Already have an entry for today?
        existing = await self.get_entry(local_date)
        return existing is None

    async def get_summary(self, days: int = 7) -> str:
        """Get a summary of recent journal entries (window in local time)."""
        target = self._to_local_time(datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%d"
        )
        rows = await self.db.fetch_all(
            "SELECT * FROM journal_entries WHERE local_date >= ? ORDER BY local_date DESC",
            (target,),
        )
        if not rows:
            return f"No journal entries found in the last {days} days."

        parts = [f"Journal summary (last {days} days):"]
        for row in rows:
            parts.append(f"\n{row['local_date']}: {row.get('summary_text', '(no summary)')}")
        return "\n".join(parts)

    async def export_all(self) -> list[dict]:
        """Export all journal entries as a list of dicts."""
        rows = await self.db.fetch_all("SELECT * FROM journal_entries ORDER BY local_date")
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
        source_event_ids = _decode_json_field(
            row.get("source_event_ids_json", "[]"), [], "source_event_ids_json"
        )
        mood_before = _decode_json_field(row.get("mood_before_json", "{}"), {}, "mood_before_json")
        mood_after = _decode_json_field(row.get("mood_after_json", "{}"), {}, "mood_after_json")

        return JournalEntry(
            id=row["id"],
            local_date=row["local_date"],
            written_at_utc=row["written_at_utc"],
            source_event_ids=source_event_ids if isinstance(source_event_ids, list) else [],
            body_text=row.get("body_text", ""),
            summary_text=row.get("summary_text", ""),
            mood_before=MoodState(**mood_before) if mood_before else MoodState(),
            mood_after=MoodState(**mood_after) if mood_after else MoodState(),
        )


def _decode_json_field(raw: object, default: object, field_name: str):
    """Decode canonical JSON, with safe support for legacy Python repr rows."""
    if not isinstance(raw, str):
        logger.warning("Invalid non-string journal field %s", field_name)
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            legacy_value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            logger.warning("Invalid journal JSON in %s", field_name)
            return default
        logger.warning("Loaded legacy non-JSON journal field %s", field_name)
        return legacy_value
