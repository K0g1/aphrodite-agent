"""Core types for Aphrodite Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex[:24]


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class EventType(str, Enum):
    ROUTINE_COMPLETED = "routine_completed"
    WEATHER_SNAPSHOT = "weather_snapshot"
    PROCEDURAL_LOCAL = "procedural_local"
    CHARACTER_INITIATED = "character_initiated"
    SOCIAL_INTERACTION = "social_interaction"
    PROJECT_PROGRESS = "project_progress"
    CONVERSATION = "conversation"
    JOURNAL = "journal"
    SYSTEM = "system"


class EventStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class Provenance(str, Enum):
    CALENDAR = "calendar"
    CURATED_REAL = "curated_real"
    PROCEDURAL_FICTIONAL = "procedural_fictional"
    CHARACTER_INITIATED = "character_initiated"
    USER_SUPPLIED = "user_supplied"
    SYSTEM = "system"


class MemoryType(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    CORRECTION = "correction"
    EVENT = "event"
    BOUNDARY = "boundary"
    RELATIONSHIP = "relationship"
    PROJECT = "project"
    MOOD = "mood"
    OPEN_LOOP = "open_loop"


class Sensitivity(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    PRIVATE = "private"


class PartOfDay(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


@dataclass
class MoodState:
    valence: float = 0.15
    arousal: float = 0.40
    dominance: float = 0.50
    affection: float = 0.55
    trust: float = 0.50
    curiosity: float = 0.65

    def to_dict(self) -> dict[str, float]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "dominance": self.dominance,
            "affection": self.affection,
            "trust": self.trust,
            "curiosity": self.curiosity,
        }

    def label(self) -> str:
        if self.valence > 0.3 and self.energy > 0.5:
            return "positive and energetic"
        if self.valence > 0.1:
            return "quietly positive"
        if self.valence < -0.2:
            return "low and subdued"
        if self.arousal > 0.6:
            return "alert and engaged"
        return "neutral and calm"

    @property
    def energy(self) -> float:
        return (self.arousal + self.valence) / 2


@dataclass
class WorldWeather:
    condition: str = "partly_cloudy"
    temperature_c: float = 20.0
    precipitation: str = "none"
    wind: str = "light"
    valid_from: str = ""
    valid_until: str = ""


@dataclass
class WorldState:
    location_id: str = "place.home"
    activity: str = "having a quiet pause"
    activity_started: str = ""
    mood: MoodState = field(default_factory=MoodState)
    weather: WorldWeather = field(default_factory=WorldWeather)
    last_processed_utc: str = ""
    current_setting: str = "the familiar setting"


@dataclass
class ConversationTurn:
    id: str = field(default_factory=new_id)
    conversation_id: str = ""
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp_utc: str = ""
    token_count: int = 0


@dataclass
class Memory:
    id: str = field(default_factory=new_id)
    memory_type: MemoryType = MemoryType.FACT
    content: str = ""
    confidence: float = 0.9
    importance: float = 0.5
    sensitivity: Sensitivity = Sensitivity.NORMAL
    source_message_id: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    entities: list[str] = field(default_factory=list)


@dataclass
class JournalEntry:
    id: str = field(default_factory=new_id)
    local_date: str = ""
    source_event_ids: list[str] = field(default_factory=list)
    body_text: str = ""
    summary_text: str = ""
    mood_before: MoodState = field(default_factory=MoodState)
    mood_after: MoodState = field(default_factory=MoodState)
    written_at_utc: str = ""


@dataclass
class ContextPacket:
    """Compiled context ready for prompt assembly."""
    system_prompt: str = ""
    date_time_line: str = ""
    character_identity: str = ""
    personality: str = ""
    speech_style: str = ""
    emotional_state: str = ""
    short_term_memory: str = ""
    long_term_memory: str = ""
    recent_conversation: list[ConversationTurn] = field(default_factory=list)
    total_tokens: int = 0
