"""Proactive messaging — character initiates conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import Config, ProactiveConfig
from ..db.database import Database
from ..character import Character
from ..types import MoodState, WorldState, new_id
from ..world import WorldEngine


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
        self._last_check_hour = -1

    async def think_about_messaging(self, character: Character,
                                      world: WorldState,
                                      mood: MoodState,
                                      now_utc: datetime | None = None) -> ProactiveMessage | None:
        """Decide if the character should message the user."""
        config = self.config.proactive
        if not config.enabled:
            return None

        now = now_utc or datetime.now(timezone.utc)

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
        msg_type = self._choose_message_type(now, mood, character)

        # 5. Generate content
        content = self._generate_message(msg_type, character, world)

        if not content:
            return None

        message = ProactiveMessage(
            message_type=msg_type,
            content=content,
            created_utc=now.isoformat(),
        )

        # Log to DB
        await self.db.execute(
            "INSERT INTO proactive_events (id, event_type, status, title, scheduled_at, created_at_utc) VALUES (?, ?, 'sent', ?, ?, ?)",
            (message.id, msg_type, content[:80], now.isoformat(), now.isoformat()),
        )
        await self.db.commit()

        return message

    def _choose_message_type(self, now: datetime, mood: MoodState,
                               character: Character) -> str:
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
            if mood.valence > 0.3:
                return "share_from_life"
            return "check_in"

        return "check_in"

    def _generate_message(self, msg_type: str, character: Character,
                            world: WorldState) -> str:
        """Generate message content."""
        templates = PROACTIVE_PROMPTS.get(msg_type, PROACTIVE_PROMPTS["check_in"])
        template = templates[hash(character.name + datetime.now().strftime("%Y%m%d")) % len(templates)]

        # Fill in variables
        topic = "that thing"
        activity = world.activity or "resting"
        content = template.format(topic=topic, activity=activity)

        return content

    def _is_in_waking_hours(self, now: datetime) -> bool:
        """Check if current time is not in quiet hours."""
        config = self.config.proactive
        hour = now.hour
        start_h = int(config.quiet_hours_start.split(":")[0])
        end_h = int(config.quiet_hours_end.split(":")[0])

        if start_h < end_h:  # Same day range
            return hour < start_h or hour >= end_h
        else:  # Crosses midnight
            return hour >= end_h or hour < start_h

    async def _get_recent_count(self, now: datetime) -> int:
        """Count proactive messages sent today."""
        today = now.strftime("%Y-%m-%d")
        rows = await self.db.fetch_all(
            "SELECT COUNT(*) as c FROM proactive_events WHERE event_type != 'system' AND created_at_utc >= ?",
            (today,),
        )
        return rows[0]["c"] if rows else 0

    async def _get_last_message_time(self) -> datetime | None:
        """Get the time of the last message."""
        row = await self.db.fetch_one(
            "SELECT created_at_utc FROM proactive_events WHERE event_type != 'system' ORDER BY created_at_utc DESC LIMIT 1"
        )
        if row and row.get("created_at_utc"):
            try:
                return datetime.fromisoformat(row["created_at_utc"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        return None
