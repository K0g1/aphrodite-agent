"""World state engine — deterministic simulation of the character's world."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta

from ..config import Config
from ..db.database import Database
from ..types import (
    MoodState,
    WorldState,
    WorldWeather,
    new_id,
)

logger = logging.getLogger("aphrodite.world")
DEFAULT_WORLD_SEED = "aphrodite-default"


def _coerce_utc_datetime(value: datetime | str) -> datetime:
    """Parse a datetime value and normalize it to timezone-aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise TypeError("Expected datetime or ISO-8601 string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validate_advance_hours(hours: object) -> int | float:
    """Validate hours at the engine boundary as defense in depth."""
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        raise TypeError("hours must be a positive finite number")
    if isinstance(hours, float) and not math.isfinite(hours):
        raise ValueError("hours must be a positive finite number")
    if hours <= 0:
        raise ValueError("hours must be a positive finite number")
    try:
        timedelta(hours=hours)
    except (OverflowError, ValueError) as exc:
        raise ValueError("hours is too large") from exc
    return hours


class WorldEngine:
    """Deterministic world state engine."""

    _world_secret: str

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self._world_seed = DEFAULT_WORLD_SEED
        self._advance_lock = asyncio.Lock()

    async def get_state(self) -> WorldState:
        """Get current world state."""
        row = await self.db.get_world_state()
        if not row:
            return WorldState()

        # Tolerate corrupt/legacy JSON in the state columns: a bad value must
        # not brick the whole agent (mirrors journal._decode_json_field).
        try:
            mood_data = json.loads(row.get("mood_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            mood_data = {}
        try:
            weather_data = json.loads(row.get("weather_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            weather_data = {}

        mood = MoodState(
            valence=mood_data.get("valence", 0.15),
            arousal=mood_data.get("arousal", 0.40),
            dominance=mood_data.get("dominance", 0.50),
            affection=mood_data.get("affection", 0.55),
            trust=mood_data.get("trust", 0.50),
            curiosity=mood_data.get("curiosity", 0.65),
        )

        weather = WorldWeather(
            condition=weather_data.get("condition", "partly_cloudy"),
            temperature_c=weather_data.get("temperature_c", 20.0),
            precipitation=weather_data.get("precipitation", "none"),
            wind=weather_data.get("wind", "light"),
        )

        return WorldState(
            location_id=row.get("current_location_id", "place.home"),
            activity=row.get("current_activity", "quiet pause"),
            activity_started=row.get("activity_started_utc", ""),
            mood=mood,
            weather=weather,
            last_processed_utc=row.get("last_processed_utc", ""),
            current_setting=row.get("current_setting", "home"),
        )

    async def update_state(self, now_utc: datetime) -> list[dict]:
        """Advance the world state to the current time. Returns list of events generated."""
        async with self._advance_lock:
            return await self._update_state_unlocked(now_utc)

    async def _update_state_unlocked(self, now_utc: datetime) -> list[dict]:
        """Advance the world state; caller must hold ``_advance_lock``."""
        now_utc = _coerce_utc_datetime(now_utc)
        state = await self.get_state()
        events: list[dict] = []
        interval_seconds = self.config.world.state_update_interval_minutes * 60

        # Calculate time since last update
        if state.last_processed_utc:
            try:
                last = _coerce_utc_datetime(state.last_processed_utc)
                elapsed_seconds = (now_utc - last).total_seconds()
                elapsed = elapsed_seconds / 3600
            except (ValueError, TypeError):
                elapsed = 0
                elapsed_seconds = 0
        else:
            elapsed = 0
            # Force one initialization pass so the persisted clock cannot remain blank.
            elapsed_seconds = interval_seconds

        # Detect negative elapsed (update is earlier than simulation clock)
        if elapsed_seconds < 0:
            logger.warning(
                "Ignoring world update earlier than simulation clock",
                extra={
                    "requested_utc": now_utc.isoformat(),
                    "last_processed_utc": state.last_processed_utc,
                    "elapsed_seconds": elapsed_seconds,
                },
            )
            return events

        # Cap catch-up so a long offline gap does not snap mood/state in one tick.
        max_catchup_seconds = self.config.world.max_catchup_hours * 3600
        if elapsed_seconds > max_catchup_seconds:
            elapsed_seconds = max_catchup_seconds
            elapsed = max_catchup_seconds / 3600

        # Only update if enough time has passed
        if elapsed_seconds < interval_seconds:
            return events

        # Update mood (decay toward baseline)
        state.mood = self._decay_mood(state.mood, elapsed)

        # Update weather (simple daily cycle)
        state.weather = self._generate_weather(now_utc, state.weather)

        # Update activity based on time of day
        local_time = self._to_local_time(now_utc)
        new_activity = self._get_scheduled_activity(local_time)
        if new_activity != state.activity:
            state.activity = new_activity
            events.append(
                {
                    "event_type": "activity_change",
                    "summary": f"Changed activity to: {new_activity}",
                    "local_time": local_time.strftime("%H:%M"),
                }
            )

        # Persist
        updates: dict[str, str] = {
            "last_processed_utc": now_utc.isoformat(),
            "mood_json": json.dumps(state.mood.to_dict()),
            "weather_json": json.dumps(
                {
                    "condition": state.weather.condition,
                    "temperature_c": state.weather.temperature_c,
                    "precipitation": state.weather.precipitation,
                    "wind": state.weather.wind,
                }
            ),
            "current_setting": state.current_setting,
            "updated_at_utc": now_utc.isoformat(),
        }
        # activity_started_utc means "when the CURRENT activity began": only
        # rewrite it when the activity actually changed.
        if "activity_change" in {e["event_type"] for e in events}:
            updates["current_activity"] = state.activity
            updates["activity_started_utc"] = now_utc.isoformat()
        await self.db.update_world_state(**updates)

        # Log events to database
        for evt in events:
            event_id = new_id()
            await self.db.save_event(
                event_id=event_id,
                event_type=evt["event_type"],
                layer=1,
                provenance="system",
                status="completed",
                title=evt["event_type"],
                summary=evt["summary"],
                starts_at=now_utc.isoformat(),
                local_date=local_time.strftime("%Y-%m-%d"),
            )

        return events

    async def advance_time(self, hours: float, now_utc: datetime | None = None) -> list[dict]:
        """Advance world by N hours (for simulation). Uses stored simulation clock."""
        hours = _validate_advance_hours(hours)

        async with self._advance_lock:
            wall_now = _coerce_utc_datetime(now_utc or datetime.now(UTC))
            state = await self.get_state()

            # Determine the current simulation time from the stored clock
            if state.last_processed_utc:
                try:
                    simulation_now = _coerce_utc_datetime(state.last_processed_utc)
                except (TypeError, ValueError):
                    # Corrupt persisted clock — recover from wall time
                    simulation_now = wall_now
                    await self.db.update_world_state(
                        last_processed_utc=simulation_now.isoformat(),
                        updated_at_utc=wall_now.isoformat(),
                    )
            else:
                # No stored clock — bootstrap from wall time
                simulation_now = wall_now
                await self.db.update_world_state(
                    last_processed_utc=simulation_now.isoformat(),
                    updated_at_utc=wall_now.isoformat(),
                )

            target = simulation_now + timedelta(hours=hours)
            # Lock already held: use the unlocked variant to avoid deadlock.
            return await self._update_state_unlocked(target)

    def get_previous_activity(self, state: WorldState) -> str:
        """Generate a plausible 'before the user messaged' description."""
        activities = [
            "reading on the sofa",
            "making tea in the kitchen",
            "listening to music",
            "working on a sketch",
            "tidying up the living room",
            "watching the clouds from the window",
            "organizing some notes",
            "resting between activities",
            "thinking about a conversation from earlier",
        ]
        # Deterministic selection based on current time
        seed = f"{self._get_world_seed()}|activity|{state.activity}"
        idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(activities)
        return activities[idx]

    def _decay_mood(self, mood: MoodState, hours_elapsed: float) -> MoodState:
        """Decay mood toward the configured baseline over time."""
        mcfg = self.config.mood
        baseline = MoodState(
            valence=mcfg.baseline_valence,
            arousal=mcfg.baseline_arousal,
            dominance=mcfg.baseline_dominance,
            affection=mcfg.baseline_affection,
            trust=mcfg.baseline_trust,
            curiosity=mcfg.baseline_curiosity,
        )

        # Decay rates (fraction per hour)
        decay_rates = {
            "valence": 0.08,
            "arousal": 0.15,
            "dominance": 0.05,
            "affection": 0.02,
            "trust": 0.01,
            "curiosity": 0.05,
        }

        def decay(current: float, base: float, rate: float) -> float:
            factor = (1 - rate) ** hours_elapsed
            return base + (current - base) * factor

        return MoodState(
            valence=max(
                -1.0, min(1.0, decay(mood.valence, baseline.valence, decay_rates["valence"]))
            ),
            arousal=max(
                0.0, min(1.0, decay(mood.arousal, baseline.arousal, decay_rates["arousal"]))
            ),
            dominance=max(
                0.0, min(1.0, decay(mood.dominance, baseline.dominance, decay_rates["dominance"]))
            ),
            affection=max(
                0.0, min(1.0, decay(mood.affection, baseline.affection, decay_rates["affection"]))
            ),
            trust=max(0.0, min(1.0, decay(mood.trust, baseline.trust, decay_rates["trust"]))),
            curiosity=max(
                0.0, min(1.0, decay(mood.curiosity, baseline.curiosity, decay_rates["curiosity"]))
            ),
        )

    def _generate_weather(self, now_utc: datetime, current: WorldWeather) -> WorldWeather:
        """Generate weather based on time of day and season."""
        local = self._to_local_time(now_utc)
        month = local.month

        # Simple seasonal temperature
        base_temps = {
            1: 3,
            2: 5,
            3: 7,
            4: 10,
            5: 14,
            6: 17,
            7: 20,
            8: 20,
            9: 16,
            10: 11,
            11: 7,
            12: 4,
        }
        base = base_temps.get(month, 15)

        # Time-of-day variation
        hour = local.hour
        if 6 <= hour < 12:
            temp_offset = 2
        elif 12 <= hour < 18:
            temp_offset = 4
        elif 18 <= hour < 22:
            temp_offset = 1
        else:
            temp_offset = -1

        effective_temp = base + temp_offset

        # Deterministic weather condition seeded on the LOCAL date so the
        # day boundary matches the local calendar, like the temperature.
        seed = f"{self._get_world_seed()}|weather|{local.strftime('%Y-%m-%d')}"
        h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
        rain_chance = 0.3 if month in [10, 11, 12, 1, 2, 3] else 0.1
        conditions = ["partly_cloudy", "cloudy", "clear", "rain", "light_rain"]
        weights = [0.35, 0.25, 0.30, rain_chance, rain_chance * 0.5]
        total_w = sum(weights)
        r = (h % 1000) / 1000 * total_w
        cumulative = 0.0
        condition = "partly_cloudy"
        for c, w in zip(conditions, weights):
            cumulative += w
            if r < cumulative:
                condition = c
                break

        return WorldWeather(
            condition=condition,
            temperature_c=float(round(effective_temp, 1)),
            precipitation="rain" if "rain" in condition else "none",
            wind="light" if condition != "rain" else "moderate",
        )

    def _get_scheduled_activity(self, local_time: datetime) -> str:
        """Get activity based on time of day."""
        hour = local_time.hour
        if 23 <= hour or hour < 7:
            return "sleeping"
        elif 7 <= hour < 8:
            return "waking up slowly"
        elif 8 <= hour < 9:
            return "getting ready for the day"
        elif 9 <= hour < 12:
            return "focused on work"
        elif 12 <= hour < 13:
            return "taking a lunch break"
        elif 13 <= hour < 17:
            return "working in the afternoon"
        elif 17 <= hour < 18:
            return "heading home"
        elif 18 <= hour < 19:
            return "making dinner"
        elif 19 <= hour < 21:
            return "relaxing in the evening"
        elif 21 <= hour < 22:
            return "winding down"
        else:
            return "getting ready for bed"

    def _to_local_time(self, utc_time: datetime) -> datetime:
        """Convert UTC time to the configured local timezone."""
        utc_time = _coerce_utc_datetime(utc_time)
        if self.config.timezone == "system":
            return utc_time.astimezone()
        from zoneinfo import ZoneInfo

        return utc_time.astimezone(ZoneInfo(self.config.timezone))

    def _get_world_seed(self) -> str:
        """Return the seed while preserving the legacy direct-test seam."""
        return getattr(self, "_world_seed", getattr(self, "_world_secret", DEFAULT_WORLD_SEED))
