"""Integration tests for Aphrodite Agent."""

from datetime import UTC, datetime

import pytest

from aphrodite.character import (
    Character,
    CharacterIdentity,
    EmotionalModel,
    PersonalitySliders,
    SpeechStyle,
)
from aphrodite.config import Config, MoodConfig
from aphrodite.context import PersonalityRenderer, assemble_prompt
from aphrodite.extraction import MemoryExtractor
from aphrodite.journal import JournalManager
from aphrodite.mood import MoodManager
from aphrodite.simulation import MockProvider, SimulatedClock
from aphrodite.types import Memory, MemoryType, MoodState, WorldState


class TestMemoryExtractor:
    def test_keyword_extraction_preference(self):
        extractor = MemoryExtractor()
        user = "I really like quiet mornings."
        assistant = "That's nice."
        memories = extractor._extract_with_keywords(user, assistant)
        assert len(memories) >= 1
        assert memories[0].memory_type == MemoryType.PREFERENCE

    def test_keyword_extraction_fact(self):
        extractor = MemoryExtractor()
        user = "I work at Vancouver General Hospital."
        assistant = "Oh, that's interesting."
        memories = extractor._extract_with_keywords(user, assistant)
        assert len(memories) >= 1
        assert "work" in memories[0].content.lower()

    def test_keyword_extraction_open_loop(self):
        extractor = MemoryExtractor()
        user = "I will submit my application tomorrow."
        assistant = "Good luck with it."
        memories = extractor._extract_with_keywords(user, assistant)
        assert len(memories) >= 1
        assert memories[0].memory_type == MemoryType.OPEN_LOOP

    def test_empty_message(self):
        extractor = MemoryExtractor()
        memories = extractor._extract_with_keywords("Thanks!", "You're welcome.")
        assert len(memories) == 0


class TestJournalManager:
    @pytest.mark.asyncio
    async def test_is_due(self, tmp_path):
        from aphrodite.db import Database

        db = Database(tmp_path / "journal.db")
        await db.initialize()

        config = Config()
        config.world.journal_time = "21:30"
        journal = JournalManager(db, config)

        # At 3 AM UTC (8 PM Pacific, before journal time), should not be due
        early = datetime(2026, 7, 22, 3, 0, tzinfo=UTC)
        assert not await journal.is_due(early)

        # At 5 AM UTC (10 PM Pacific, after 21:30 journal time), should be due
        late = datetime(2026, 7, 23, 5, 0, tzinfo=UTC)  # 10 PM Pacific on July 22
        assert await journal.is_due(late, timezone_str="America/Vancouver")

        await db.close()

    def test_summarize(self):
        journal = JournalManager.__new__(JournalManager)
        text = "Today was a good day. I went for a walk and read a book. Feeling pretty content."
        summary = journal._summarize(text)
        assert len(summary) > 0
        assert "good day" in summary.lower()


class TestSimulation:
    @pytest.mark.asyncio
    async def test_simulated_clock(self):
        clock = SimulatedClock(
            start_utc=datetime(2026, 7, 1, tzinfo=UTC),
            speed=100,
        )
        now = clock.now_utc()
        assert now.month == 7

        clock.advance(360)  # 360 real seconds at 100x = 10 hours sim
        later = clock.now_utc()
        assert later.hour == (now.hour + 10) % 24 or later.day == now.day + 1

    def test_mock_provider(self):
        provider = MockProvider()
        import asyncio

        response = asyncio.run(provider.complete([{"role": "user", "content": "Hello"}]))
        assert isinstance(response, str)
        assert len(response) > 0

    def test_mock_provider_deterministic(self):
        provider = MockProvider()
        import asyncio

        responses = set()
        for i in range(10):
            r = asyncio.run(provider.complete([{"role": "user", "content": f"Test {i}"}]))
            responses.add(r)
        # Should return a variety of responses
        assert len(responses) > 1

    def test_mock_provider_failure(self):
        provider = MockProvider()
        provider._failure_rate = 1.0
        import asyncio

        with pytest.raises(RuntimeError):
            asyncio.run(provider.complete([{"role": "user", "content": "Hello"}]))


class TestPersonalityRenderer:
    def test_render_all_sliders(self):
        renderer = PersonalityRenderer()
        sliders = PersonalitySliders(
            warmth=0.7,
            directness=0.5,
            playfulness=0.4,
            expressiveness=0.6,
            initiative=0.4,
            verbosity=0.4,
            formality=0.3,
            flirtation=0.0,
        )
        result = renderer.render(sliders)
        assert "Warmth" in result
        assert "Directness" in result
        assert "Playfulness" in result
        assert "Flirtation" in result
        assert "Not flirtatious" in result

    def test_slider_boundaries(self):
        renderer = PersonalityRenderer()
        for val, expected_label in [
            (0.0, "very low"),
            (0.3, "low"),
            (0.5, "moderate"),
            (0.7, "high"),
            (1.0, "very high"),
        ]:
            sliders = PersonalitySliders(warmth=val)
            result = renderer.render(sliders)
            band_idx = renderer._band_index(val)
            from aphrodite.context import SLIDER_DESCRIPTIONS

            expected = SLIDER_DESCRIPTIONS["warmth"][band_idx]
            assert expected in result


class TestMoodSystem:
    def test_mood_labels(self):
        moods = [
            (MoodState(valence=0.5, arousal=0.7), "energetic"),
            (MoodState(valence=-0.3, arousal=0.2), "subdued"),
            (MoodState(valence=0.0, arousal=0.3), "neutral"),
        ]
        labels = []
        for mood, _ in moods:
            labels.append(mood.label())
        for label in labels:
            assert isinstance(label, str)
            assert len(label) > 0

    def test_mood_decay_limits(self):
        config = MoodConfig()
        manager = MoodManager(config)
        mood = MoodState(valence=1.0, arousal=1.0)
        # Multiple small impacts should not exceed max
        for _ in range(20):
            mood = manager.apply_event_impact(mood, -0.1, -0.1)
        assert mood.valence >= -1.0
        assert mood.arousal >= 0.0

    def test_mood_to_dict(self):
        mood = MoodState(valence=0.3, arousal=0.6)
        d = mood.to_dict()
        assert d["valence"] == 0.3
        assert d["arousal"] == 0.6
        assert len(d) == 6  # All 6 dimensions


class TestPromptAssembly:
    def test_assembly_with_full_character(self):
        character = Character(
            identity=CharacterIdentity(
                name="Mira",
                pronouns="she/her",
                age=24,
                core_identity="A warm, thoughtful companion",
                values=["honesty", "kindness"],
                likes=["reading", "coffee"],
                dislikes=["dishonesty"],
                boundaries=["respect privacy"],
            ),
            personality=PersonalitySliders(warmth=0.7),
            speech=SpeechStyle(
                register="casual",
                vocabulary="natural",
                mannerisms=["uses 'hmm' when thinking"],
            ),
            emotion=EmotionalModel(baseline="calm"),
        )
        now = datetime(2026, 7, 22, 14, 0, tzinfo=UTC)

        result = assemble_prompt(
            character=character,
            mood=MoodState(valence=0.5, arousal=0.4),
            world=WorldState(activity="reading at the cafe", current_setting="cafe"),
            short_term_memories=[
                Memory(content="User prefers tea over coffee.", memory_type=MemoryType.PREFERENCE)
            ],
            long_term_memories=[
                Memory(content="User is working on a research project.", importance=0.8)
            ],
            recent_turns=[],
            current_user_message="Hi Mira, how are you?",
            now=now,
        )

        assert "Mira" in result.system_prompt
        assert "she/her" in result.system_prompt
        assert "Hi Mira" in result.messages[-1]["content"]
        assert result.total_input_tokens > 0

    def test_assembly_with_empty_memories(self):
        character = Character(
            identity=CharacterIdentity(name="Test", core_identity="Test character"),
            personality=PersonalitySliders(),
        )
        result = assemble_prompt(
            character=character,
            mood=MoodState(),
            world=WorldState(),
            short_term_memories=[],
            long_term_memories=[],
            recent_turns=[],
            current_user_message="Hello",
            now=datetime(2026, 7, 22, tzinfo=UTC),
        )
        assert result.total_input_tokens > 0
        assert result.system_prompt

    def test_token_budget_enforcement(self):
        character = Character(
            identity=CharacterIdentity(name="Test", core_identity="Test character"),
            personality=PersonalitySliders(),
        )
        result = assemble_prompt(
            character=character,
            mood=MoodState(),
            world=WorldState(),
            short_term_memories=[],
            long_term_memories=[],
            recent_turns=[],
            current_user_message="Hello",
            now=datetime(2026, 7, 22, tzinfo=UTC),
            max_input_tokens=4096,
        )
        assert result.total_input_tokens <= 4096
