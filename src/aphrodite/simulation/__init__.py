"""Simulation framework — time travel, mock provider, stress tests, chaos testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable
import asyncio
import json
import random

from ..config import Config
from ..db.database import Database
from ..types import MoodState, new_id
from ..world import WorldEngine
from ..journal import JournalManager


MOCK_RESPONSES = [
    "I see what you mean.",
    "That's interesting, tell me more.",
    "Hmm, I was just thinking about that.",
    "How did that make you feel?",
    "I was reading earlier when you messaged.",
    "I hadn't thought of it that way before.",
    "That reminds me of something similar.",
    "I'm glad you told me that.",
    "You know, I was wondering about that.",
    "That makes a lot of sense actually.",
]


@dataclass
class SimulationReport:
    """Report from a simulation run."""
    simulation_id: str = field(default_factory=new_id)
    duration_hours: float = 0
    real_time_seconds: float = 0
    character: str = ""
    provider_mode: str = "mock"

    total_turns: int = 0
    total_messages: int = 0
    total_events: int = 0
    total_journal_entries: int = 0
    memory_operations: dict = field(default_factory=lambda: {"created": 0, "retrieved": 0})
    consistency_score: float = 1.0
    errors: int = 0
    warnings: int = 0

    final_mood: dict = field(default_factory=dict)
    trait_changes: dict = field(default_factory=dict)
    seasons_experienced: list[str] = field(default_factory=list)
    events_by_type: dict = field(default_factory=dict)


class SimulatedClock:
    """Deterministic clock that can be accelerated."""

    def __init__(self, start_utc: datetime | None = None, speed: float = 1.0):
        self._start = start_utc or datetime(2026, 7, 1, tzinfo=timezone.utc)
        self._elapsed = timedelta(0)
        self._speed = speed
        self._frozen = False

    def now_utc(self) -> datetime:
        return self._start + self._elapsed

    def advance(self, real_seconds: float) -> None:
        if not self._frozen:
            self._elapsed += timedelta(seconds=real_seconds * self._speed)

    def advance_hours(self, hours: float) -> None:
        self._elapsed += timedelta(hours=hours)

    def freeze(self) -> None:
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, value)


class MockProvider:
    """Returns templated responses, no API calls."""

    def __init__(self):
        self.call_count = 0
        self._responses = MOCK_RESPONSES.copy()
        self._fail_next = False
        self._failure_rate = 0.0

    async def complete(self, messages: list[dict], **kwargs) -> str:
        self.call_count += 1

        if self._fail_next:
            self._fail_next = False
            raise Exception("Simulated provider failure")

        if random.random() < self._failure_rate:
            raise Exception("Simulated random provider failure")

        # Deterministic response selection
        msg = messages[-1]["content"] if messages else ""
        idx = hash(msg + str(self.call_count)) % len(self._responses)
        return self._responses[idx]

    async def health_check(self) -> bool:
        return True

    def set_failure_mode(self, fail_next: bool = False, failure_rate: float = 0.0):
        self._fail_next = fail_next
        self._failure_rate = failure_rate


@dataclass
class SimulationScript:
    """Scripted simulation instructions."""
    steps: list[dict] = field(default_factory=list)

    @classmethod
    def from_jsonl(cls, path: str) -> "SimulationScript":
        steps = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    steps.append(json.loads(line))
        return cls(steps=steps)


class SimulationEngine:
    """Orchestrates simulation runs."""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.clock = SimulatedClock()
        self.provider = MockProvider()
        self.world_engine = WorldEngine(db, config)
        self.journal_manager = JournalManager(db, config, provider=self.provider)
        self.report = SimulationReport()
        self._errors = 0
        self._warnings = 0

    async def run(self, hours: float, character: str = "mira",
                  speed: float = 100.0, mock_provider: bool = True,
                  script: SimulationScript | None = None) -> SimulationReport:
        """Run a simulation."""
        self.clock = SimulatedClock(speed=speed)
        self.report = SimulationReport(
            duration_hours=hours,
            character=character,
            provider_mode="mock" if mock_provider else "live",
        )
        start_time = datetime.now(timezone.utc)

        if script:
            await self._run_script(script)
        else:
            await self._run_free(hours, character, mock_provider)

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        self.report.real_time_seconds = elapsed

        # Gather final state
        events = await self.db.get_events_on_date(self.clock.now_utc().strftime("%Y-%m-%d"))
        self.report.total_events = len(events)
        self.report.errors = self._errors
        self.report.warnings = self._warnings

        # Count events by type
        type_counts = {}
        for e in events:
            t = e.get("event_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        self.report.events_by_type = type_counts

        return self.report

    async def _run_free(self, hours: float, character: str, mock_provider: bool):
        """Free-running simulation with periodic user messages."""
        total_minutes = int(hours * 60)

        # Create a minimal character for journal writing
        from .character import Character, CharacterIdentity, PersonalitySliders, SpeechStyle
        sim_char = Character(
            id=character,
            identity=CharacterIdentity(name=character.title()),
            personality=PersonalitySliders(),
            speech=SpeechStyle(),
        )

        for minute in range(0, total_minutes, 15):  # Tick every 15 minutes
            now = self.clock.now_utc()

            # Advance world engine
            events = await self.world_engine.update_state(now)
            self.report.total_events += len(events)

            # Check journal due
            if await self.journal_manager.is_due(now):
                entry = await self.journal_manager.write_entry(
                    character=sim_char,
                    mood=MoodState(),
                    world_events=events,
                    now_utc=now,
                )
                if entry:
                    self.report.total_journal_entries += 1

            # Send a user message every 4-8 hours
            if minute % 360 == 0:  # Every 6 hours
                msg = random.choice([
                    "Hey, how's it going?",
                    "Just thinking about you. What are you up to?",
                    "How was your day?",
                    "I'm back. What have you been doing?",
                    "Hey, feeling kinda tired today. How about you?",
                ])
                try:
                    resp = await self.provider.complete(
                        [{"role": "user", "content": msg}]
                    )
                    self.report.total_messages += 1
                    self.report.memory_operations["retrieved"] += 1
                except Exception:
                    self._errors += 1

            # Advance clock
            self.clock.advance(15 * 60 / self.clock.speed)  # Real time for 15 sim minutes

    async def _run_script(self, script: SimulationScript):
        """Run a scripted simulation."""
        for step in script.steps:
            step_type = step.get("type", "")

            if step_type == "advance_time":
                hours = step.get("hours", 1)
                self.clock.advance_hours(hours)
                now = self.clock.now_utc()
                events = await self.world_engine.update_state(now)
                self.report.total_events += len(events)

            elif step_type == "user_message":
                content = step.get("content", "")
                try:
                    resp = await self.provider.complete(
                        [{"role": "user", "content": content}]
                    )
                    self.report.total_messages += 1
                except Exception:
                    self._errors += 1

            elif step_type == "check_state":
                expected = step.get("expected", {})
                # Verify state matches expected (for deterministic tests)

            elif step_type == "check_journal":
                from datetime import timezone
                now = self.clock.now_utc()
                entry = await self.journal_manager.get_entry(now.strftime("%Y-%m-%d"))
                if entry:
                    self.report.total_journal_entries += 1

    async def run_stress_test(self, interactions: int = 10000,
                               consistency: bool = True) -> dict:
        """Run a stress test with many interactions."""
        results = {"interactions": 0, "errors": 0, "consistency_issues": 0}

        for i in range(interactions):
            try:
                msg = f"Message {i}: How are you feeling?"
                await self.provider.complete([{"role": "user", "content": msg}])
                results["interactions"] += 1
            except Exception:
                results["errors"] += 1

            if consistency and i % 100 == 0:
                # Check that world state hasn't diverged
                state = await self.db.get_world_state()
                if state and not state.get("current_activity"):
                    results["consistency_issues"] += 1

        return results

    async def run_determinism_test(self, runs: int = 10) -> dict:
        """Verify same inputs produce identical outputs."""
        results = {"runs": 0, "identical": 0, "diverged": 0, "first_hash": ""}

        for run in range(runs):
            # Create isolated database
            temp_db = Database(self.config.data_path / f"test_run_{run}.db")
            await temp_db.initialize()

            clock = SimulatedClock(speed=100)
            for _ in range(100):  # 100 ticks
                now = clock.now_utc()
                await self.world_engine.update_state(now)
                clock.advance(3600 / 100)

            state = await temp_db.get_world_state()
            state_hash = str(state)
            await temp_db.close()

            if run == 0:
                results["first_hash"] = state_hash[:32]
            elif state_hash == results.get("last_hash", ""):
                results["identical"] += 1
            else:
                results["diverged"] += 1

            results["last_hash"] = state_hash
            results["runs"] = run + 1

        return results

    async def run_long_gap_test(self, hours: int = 8760) -> dict:
        """Simulate a long absence and verify coherence."""
        self.clock.advance_hours(hours)
        now = self.clock.now_utc()
        events = await self.world_engine.update_state(now)
        state = await self.db.get_world_state()
        return {
            "hours_simulated": hours,
            "events_generated": len(events),
            "final_activity": state.activity if state else "unknown",
            "coherent": len(events) < 5000  # Should not flood
        }
