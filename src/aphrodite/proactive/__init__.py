"""Proactive messaging — character initiates conversations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..character import Character
from ..config import Config
from ..db.database import Database
from ..types import MoodState, WorldState, new_id

PROACTIVE_PROMPTS = {
    "morning_greeting": [
        "Good morning! I was just thinking about yesterday...",
        "Morning! How did you sleep?",
        "Rise and shine! I was just making coffee and thought of you.",
    ],
    "check_in": [
        "Hey, how's your day going? I was just wondering.",
        "What are you up to? I was finishing up some reading.",
        "Just checking in. How are things on your end?",
        "Thinking of you. Hope everything's going well.",
    ],
    "topic_follow_up": [
        "Hey, you mentioned {topic} earlier. How did that turn out?",
        "I was thinking about what you said about {topic}. Any updates?",
        "Remember you were talking about {topic}? I'm curious how it went.",
    ],
    "share_from_life": [
        "I was {activity} and it made me think of you.",
        "You know what, I was just {activity} and had a thought I wanted to share.",
        "Just had a nice moment {activity}. Made me smile.",
    ],
    "goodnight": [
        "Heading to bed soon. Hope you had a good day.",
        "I'm about to call it a night. Sleep well, okay?",
        "Getting tired over here. Just wanted to say goodnight.",
    ],
}


@dataclass
class ProactiveMessage:
    """A message the character wants to send."""

    id: str = field(default_factory=new_id)
    message_type: str = ""
    content: str = ""
    created_utc: str = ""
    sent_utc: str = ""
    state: str = "pending"  # pending, sent, cancelled


class ProactiveManager:
    """Manages proactive character messaging."""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    async def think_about_messaging(
        self,
        character: Character,
        world: WorldState,
        mood: MoodState,
        now_utc: datetime | None = None,
    ) -> ProactiveMessage | None:
        """Decide if the character should message the user."""
        config = self.config.proactive
        if not config.enabled:
            return None

        now = now_utc or datetime.now(UTC)
        local_now = self._to_local_time(now)

        # 1. Check quiet hours
        if not self._is_in_waking_hours(now):
            return None

        # 2. Check frequency limits
        recent_count = await self._get_recent_count(now)
        if recent_count >= config.max_per_day:
            return None

        # 3. Check min gap
        last_msg = await self._get_last_message_time()
        if last_msg:
            hours_since = (now - last_msg).total_seconds() / 3600
            if hours_since < config.min_gap_minutes / 60:
                return None

        # 4. Decide message type based on time and mood
        msg_type = self._choose_message_type(local_now, mood, character)
        if not msg_type:
            # No message type is allowed at this time (e.g. check-ins disabled).
            return None

        # 5. Generate content
        content = self._generate_message(msg_type, character, world, now)

        if not content:
            return None

        message = ProactiveMessage(
            message_type=msg_type,
            content=content,
            created_utc=now.isoformat(),
        )

        # Log to DB as pending: the delivery layer marks it sent only after the
        # message is actually delivered, so undelivered messages do not consume
        # the daily quota.
        await self.db.execute(
            "INSERT INTO proactive_events (id, event_type, status, title, scheduled_at, created_at_utc) VALUES (?, ?, 'pending', ?, ?, ?)",
            (message.id, msg_type, content[:80], now.isoformat(), now.isoformat()),
        )
        await self.db.commit()

        return message

    async def mark_sent(self, message_id: str) -> None:
        """Mark a proactive message as delivered."""
        from datetime import datetime

        await self.db.execute(
            "UPDATE proactive_events SET status = 'sent', sent_at_utc = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), message_id),
        )
        await self.db.commit()

    def _choose_message_type(self, now: datetime, mood: MoodState, character: Character) -> str:
        """Choose message type based on context."""
        hour = now.hour
        config = self.config.proactive

        # Morning (6-10) → greeting
        if 6 <= hour < 10 and config.allow_check_in:
            return "morning_greeting"

        # Late evening (21-23) → goodnight
        if 21 <= hour < 23 and config.allow_goodnight:
            return "goodnight"

        # Check-in during the day
        if config.allow_check_in:
            if mood.valence > 0.3 and config.allow_share_from_life:
                return "share_from_life"
            return "check_in"

        # Daytime messaging fully disabled (allow_check_in=False): no message.
        return ""

    def _generate_message(
        self, msg_type: str, character: Character, world: WorldState, now: datetime
    ) -> str:
        """Generate message content."""
        templates = PROACTIVE_PROMPTS.get(msg_type, PROACTIVE_PROMPTS["check_in"])
        seed = f"{character.name}|{msg_type}|{now.isoformat()}"
        template_index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(templates)
        template = templates[template_index]

        # Fill in variables
        topic = "that thing"
        activity = world.activity or "resting"
        content = template.format(topic=topic, activity=activity)

        return content

    def _is_in_waking_hours(self, now: datetime) -> bool:
        """Check if current time is not in quiet hours."""
        config = self.config.proactive
        local_now = self._to_local_time(now)
        current = (local_now.hour, local_now.minute)
        start = tuple(map(int, config.quiet_hours_start.split(":")))
        end = tuple(map(int, config.quiet_hours_end.split(":")))

        if start < end:
            in_quiet_hours = start <= current < end
        else:
            in_quiet_hours = current >= start or current < end
        return not in_quiet_hours

    def _to_local_time(self, now: datetime) -> datetime:
        """Convert an aware timestamp to the configured local timezone."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if self.config.timezone == "system":
            return now.astimezone()
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(self.config.timezone))

    async def _get_recent_count(self, now: datetime) -> int:
        """Count delivered proactive messages today (local day)."""
        from datetime import timedelta

        local_now = self._to_local_time(now)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        rows = await self.db.fetch_all(
            "SELECT COUNT(*) as c FROM proactive_events "
            "WHERE event_type != 'system' AND status = 'sent' "
            "AND created_at_utc >= ? AND created_at_utc < ?",
            (
                day_start.astimezone(UTC).isoformat(),
                day_end.astimezone(UTC).isoformat(),
            ),
        )
        return rows[0]["c"] if rows else 0

    async def _get_last_message_time(self) -> datetime | None:
        """Get the time of the last delivered message."""
        row = await self.db.fetch_one(
            "SELECT created_at_utc FROM proactive_events WHERE event_type != 'system' AND status = 'sent' ORDER BY created_at_utc DESC LIMIT 1"
        )
        if row and row.get("created_at_utc"):
            try:
                return datetime.fromisoformat(row["created_at_utc"])
            except (ValueError, AttributeError):
                pass
        return None
