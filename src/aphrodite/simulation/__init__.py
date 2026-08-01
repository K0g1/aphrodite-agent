"""Simulation framework — time travel, mock provider, stress tests, chaos testing."""

from __future__ import annotations

import hashlib
import json
import math
import random
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Config
from ..db.database import Database
from ..journal import JournalManager
from ..providers import Provider
from ..types import MoodState, new_id
from ..world import WorldEngine

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
    memory_operations: dict[str, int] = field(
        default_factory=lambda: {"created": 0, "retrieved": 0}
    )
    consistency_score: float = 1.0
    errors: int = 0
    warnings: int = 0

    final_mood: dict[str, object] = field(default_factory=dict)
    trait_changes: dict[str, object] = field(default_factory=dict)
    seasons_experienced: list[str] = field(default_factory=list)
    events_by_type: dict[str, int] = field(default_factory=dict)


class SimulatedClock:
    """Deterministic clock that can be accelerated."""

    def __init__(self, start_utc: datetime | None = None, speed: float = 1.0):
        self._start = start_utc or datetime(2026, 7, 1, tzinfo=UTC)
        self._elapsed = timedelta(0)
        self._speed = 1.0
        self.speed = speed  # route through the validated setter
        self._frozen = False

    def now_utc(self) -> datetime:
        return self._start + self._elapsed

    def advance(self, real_seconds: float) -> None:
        if not self._frozen:
            self._elapsed += timedelta(seconds=real_seconds * self._speed)

    def advance_hours(self, hours: float) -> None:
        if not self._frozen:
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
            raise RuntimeError("Simulated provider failure")

        # This is intentional simulation randomness, not security-sensitive entropy.
        if random.random() < self._failure_rate:  # nosec B311
            raise RuntimeError("Simulated random provider failure")

        # Deterministic response selection
        msg = messages[-1]["content"] if messages else ""
        digest = hashlib.sha256(f"{msg}|{self.call_count}".encode()).hexdigest()
        idx = int(digest, 16) % len(self._responses)
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
    def from_jsonl(cls, path: str) -> SimulationScript:
        steps = []
        with open(path) as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if line:
                    try:
                        steps.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Malformed script line {line_no}: {exc}") from exc
        return cls(steps=steps)


class SimulationEngine:
    """Orchestrates simulation runs."""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.clock = SimulatedClock()
        self.provider: MockProvider | Provider = MockProvider()
        self.world_engine = WorldEngine(db, config)
        self.journal_manager = JournalManager(db, config, provider=self.provider)
        self.report = SimulationReport()
        self._errors = 0
        self._warnings = 0
        self._consistency_checks = 0
        self._consistency_failures = 0
        self._isolate_runs = True

    async def run(
        self,
        hours: float,
        character: str = "mira",
        speed: float = 100.0,
        mock_provider: bool = True,
        script: SimulationScript | None = None,
    ) -> SimulationReport:
        """Run a simulation.

        The simulation always runs against a scratch database so that simulated
        (possibly future-dated) world state can never leak into the caller's
        production database. Pass ``isolate=False`` only in tests that need the
        original behavior.
        """
        if hours < 0:
            raise ValueError("hours must be non-negative")
        if speed <= 0:
            raise ValueError("speed must be positive")
        # NaN/inf pass the comparisons above; reject them explicitly.
        if isinstance(hours, bool) or not isinstance(hours, (int, float)):
            raise TypeError("hours must be a finite number")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise TypeError("speed must be a finite number")
        if not math.isfinite(hours) or not math.isfinite(speed):
            raise ValueError("hours and speed must be finite")
        self.clock = SimulatedClock(speed=speed)
        self.report = SimulationReport(
            duration_hours=hours,
            character=character,
            provider_mode="mock" if mock_provider else "live",
        )
        self.provider = MockProvider() if mock_provider else Provider(self.config.active_provider)
        self._errors = 0
        self._warnings = 0
        self._consistency_checks = 0
        self._consistency_failures = 0
        start_time = datetime.now(UTC)

        # Isolate the run on a scratch database (default) so the caller's DB is
        # never polluted with simulated state.
        original_db = self.db
        temp_dir: tempfile.TemporaryDirectory | None = None
        if self._isolate_runs:
            temp_dir = tempfile.TemporaryDirectory(prefix="aphrodite-sim-")
            sim_db = Database(Path(temp_dir.name) / "sim.db")
            await sim_db.initialize()
            self.db = sim_db
        else:
            sim_db = self.db
        self.world_engine = WorldEngine(sim_db, self.config)
        self.journal_manager = JournalManager(sim_db, self.config, provider=self.provider)

        try:
            if script:
                await self._run_script(script)
            else:
                await self._run_free(hours, character, mock_provider)

            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            self.report.real_time_seconds = elapsed

            self.report.errors = self._errors
            self.report.warnings = self._warnings
            if self._consistency_checks:
                self.report.consistency_score = max(
                    0.0,
                    1.0 - (self._consistency_failures / self._consistency_checks),
                )

            final_state = await sim_db.get_world_state()
            if final_state:
                try:
                    self.report.final_mood = json.loads(final_state.get("mood_json") or "{}")
                except json.JSONDecodeError:
                    self._warnings += 1
                    self.report.warnings = self._warnings
        finally:
            if isinstance(self.provider, Provider):
                await self.provider.close()
            if temp_dir is not None:
                await sim_db.close()
                temp_dir.cleanup()
                self.db = original_db
                self.world_engine = WorldEngine(original_db, self.config)
                self.journal_manager = JournalManager(
                    original_db, self.config, provider=self.provider
                )

        return self.report

    async def _run_free(self, hours: float, character: str, mock_provider: bool):
        """Free-running simulation with periodic user messages.

        Iterations step by ``15 * speed`` simulated minutes so ``speed``
        actually accelerates the run instead of being a no-op.
        """
        total_minutes = int(hours * 60)
        step_minutes = max(15, round(15 * self.clock.speed))
        # Seeded RNG for reproducible simulations (not security-sensitive).
        self._rng = random.Random(f"{self.clock.speed}|{hours}|free")  # nosec B311

        # Create a minimal character for journal writing
        from ..character import Character, CharacterIdentity, PersonalitySliders, SpeechStyle

        sim_char = Character(
            id=character,
            identity=CharacterIdentity(name=character.title()),
            personality=PersonalitySliders(),
            speech=SpeechStyle(),
        )

        last_message_mark = -1
        for minute in range(0, total_minutes, step_minutes):
            now = self.clock.now_utc()

            # Advance world engine
            events = await self.world_engine.update_state(now)
            self._record_events(events)

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

            # Send a user message every 6 simulated hours.
            six_hour_mark = minute // 360
            if six_hour_mark != last_message_mark:
                last_message_mark = six_hour_mark
                msg = self._rng.choice(
                    [
                        "Hey, how's it going?",
                        "Just thinking about you. What are you up to?",
                        "How was your day?",
                        "I'm back. What have you been doing?",
                        "Hey, feeling kinda tired today. How about you?",
                    ]
                )
                try:
                    await self.provider.complete([{"role": "user", "content": msg}])
                    self.report.total_turns += 1
                    self.report.total_messages += 2
                except Exception:  # noqa: BLE001 - simulation error counting
                    self._errors += 1

            # Advance the simulated clock by the step (in real seconds).
            self.clock.advance(step_minutes * 60 / self.clock.speed)

    async def _run_script(self, script: SimulationScript):
        """Run a scripted simulation."""
        for step in script.steps:
            step_type = step.get("type", "")

            if step_type == "advance_time":
                hours = step.get("hours", 1)
                self.clock.advance_hours(hours)
                now = self.clock.now_utc()
                events = await self.world_engine.update_state(now)
                self._record_events(events)

            elif step_type == "user_message":
                content = step.get("content", "")
                try:
                    await self.provider.complete([{"role": "user", "content": content}])
                    self.report.total_turns += 1
                    self.report.total_messages += 2
                except Exception:  # noqa: BLE001 - simulation error counting
                    self._errors += 1

            elif step_type == "check_state":
                expected = step.get("expected", {})
                state = await self.db.get_world_state() or {}
                for key, expected_value in expected.items():
                    self._consistency_checks += 1
                    if state.get(key) != expected_value:
                        self._consistency_failures += 1
                        self._warnings += 1

            elif step_type == "check_journal":
                now = self.clock.now_utc()
                local_date = self.journal_manager._to_local_time(now).strftime("%Y-%m-%d")
                entry = await self.journal_manager.get_entry(local_date)
                if entry:
                    self.report.total_journal_entries += 1

            else:
                self._warnings += 1

    def _record_events(self, events: list[dict]) -> None:
        """Accumulate event totals without replacing earlier simulation dates."""
        self.report.total_events += len(events)
        for event in events:
            event_type = event.get("event_type", "unknown")
            self.report.events_by_type[event_type] = (
                self.report.events_by_type.get(event_type, 0) + 1
            )

    async def run_stress_test(self, interactions: int = 10000, consistency: bool = True) -> dict:
        """Run a stress test with many interactions against an isolated scratch DB."""
        if interactions < 0:
            raise ValueError("interactions must be non-negative")
        results = {"interactions": 0, "errors": 0, "consistency_issues": 0}

        provider = MockProvider()
        with tempfile.TemporaryDirectory(prefix="aphrodite-stress-") as temp_root:
            db = Database(Path(temp_root) / "stress.db")
            await db.initialize()
            try:
                for i in range(interactions):
                    try:
                        msg = f"Message {i}: How are you feeling?"
                        await provider.complete([{"role": "user", "content": msg}])
                        results["interactions"] += 1
                    except Exception:  # noqa: BLE001 - simulation error counting
                        results["errors"] += 1

                    if consistency and i % 100 == 0:
                        # Check that world state hasn't diverged
                        state = await db.get_world_state()
                        if state and not state.get("current_activity"):
                            results["consistency_issues"] += 1
            finally:
                await db.close()

        return results

    async def run_determinism_test(self, runs: int = 10) -> dict[str, int | str]:
        """Verify same inputs produce identical outputs."""
        if runs <= 0:
            raise ValueError("runs must be positive")
        baseline_hash = ""
        identical = 0
        diverged = 0

        with tempfile.TemporaryDirectory(prefix="aphrodite-determinism-") as temp_root:
            for run in range(runs):
                temp_db = Database(Path(temp_root) / f"run-{run}.db")
                await temp_db.initialize()
                try:
                    isolated_engine = WorldEngine(temp_db, self.config)
                    clock = SimulatedClock()
                    for _ in range(100):
                        await isolated_engine.update_state(clock.now_utc())
                        clock.advance_hours(1)

                    state = await temp_db.get_world_state()
                    canonical_state = json.dumps(state, sort_keys=True, separators=(",", ":"))
                    state_hash = hashlib.sha256(canonical_state.encode()).hexdigest()
                finally:
                    await temp_db.close()

                if run == 0:
                    baseline_hash = state_hash
                if state_hash == baseline_hash:
                    identical += 1
                else:
                    diverged += 1

        return {
            "runs": runs,
            "identical": identical,
            "diverged": diverged,
            "first_hash": baseline_hash,
        }

    async def run_long_gap_test(self, hours: int = 8760) -> dict:
        """Simulate a long absence on an isolated scratch DB and verify coherence."""
        if hours < 0:
            raise ValueError("hours must be non-negative")
        with tempfile.TemporaryDirectory(prefix="aphrodite-longgap-") as temp_root:
            db = Database(Path(temp_root) / "gap.db")
            await db.initialize()
            try:
                engine = WorldEngine(db, self.config)
                clock = SimulatedClock()
                clock.advance_hours(hours)
                now = clock.now_utc()
                events = await engine.update_state(now)
                state = await db.get_world_state()
                row = await db.fetch_one("SELECT COUNT(*) as c FROM events")
                total_events = row["c"] if row else 0
            finally:
                await db.close()

        return {
            "hours_simulated": hours,
            "events_generated": len(events),
            "final_activity": state.get("current_activity", "unknown") if state else "unknown",
            "coherent": total_events < 5000,  # A single catch-up must not flood the timeline
        }
