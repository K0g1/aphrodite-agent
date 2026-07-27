"""Tests for Aphrodite Agent core modules."""

import pytest
from datetime import datetime, timezone

from aphrodite.config import Config, load_config
from aphrodite.types import MoodState, WorldState, Memory, new_id
from aphrodite.character import Character, PersonalitySliders, parse_character
from aphrodite.context import PersonalityRenderer, assemble_prompt, approx_tokens
from aphrodite.mood import MoodManager
from aphrodite.config import MoodConfig


class TestPersonalityRenderer:
    def test_render_warmth_high(self):
        renderer = PersonalityRenderer()
        sliders = PersonalitySliders(warmth=0.7)
        result = renderer.render(sliders)
        assert "Warm but not saccharine" in result

    def test_render_warmth_low(self):
        renderer = PersonalityRenderer()
        sliders = PersonalitySliders(warmth=0.1)
        result = renderer.render(sliders)
        assert "Reserved" in result or "cool" in result

    def test_render_flirtation_zero(self):
        renderer = PersonalityRenderer()
        sliders = PersonalitySliders(flirtation=0.0)
        result = renderer.render(sliders)
        assert "Not flirtatious" in result

    def test_band_boundaries(self):
        renderer = PersonalityRenderer()
        # Test exact boundaries
        for val, expected_band in [(0.0, 0), (0.19, 0), (0.2, 1), (0.39, 1),
                                    (0.4, 2), (0.59, 2), (0.6, 3), (0.79, 3),
                                    (0.8, 4), (1.0, 4)]:
            assert renderer._band_index(val) == expected_band


class TestMoodSystem:
    def test_decay_toward_baseline(self):
        config = MoodConfig()
        manager = MoodManager(config)
        mood = MoodState(valence=0.8, arousal=0.8)  # Elevated
        decayed = manager.apply_event_impact(mood, 0, 0)  # No new impact
        # Should still be elevated (no decay in apply_event_impact)
        assert decayed.valence == 0.8

    def test_event_impact_bounded(self):
        config = MoodConfig()
        manager = MoodManager(config)
        mood = MoodState()
        impacted = manager.apply_event_impact(mood, valence_delta=0.5)
        # Should be clamped by max_delta_per_turn
        assert abs(impacted.valence - mood.valence) <= config.max_delta_per_turn + 0.01


class TestWorldEngine:
    def test_activity_by_time(self):
        from aphrodite.world import WorldEngine
        from aphrodite.config import Config
        from datetime import time

        config = Config()
        engine = WorldEngine.__new__(WorldEngine)
        engine.config = config

        # Test different times
        t1 = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
        assert "work" in engine._get_scheduled_activity(t1).lower() or "focused" in engine._get_scheduled_activity(t1).lower()

        t2 = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
        assert "sleep" in engine._get_scheduled_activity(t2).lower()


class TestMemorySystem:
    def test_approx_tokens(self):
        assert approx_tokens("") == 0
        assert approx_tokens("Hello world") == 3
        assert approx_tokens("a" * 100) == 25


class TestPromptAssembly:
    def test_basic_assembly(self):
        from aphrodite.character import CharacterIdentity, SpeechStyle, EmotionalModel
        character = Character(
            identity=CharacterIdentity(name="Mira", pronouns="she/her", core_identity="A warm companion"),
            personality=PersonalitySliders(warmth=0.7),
            speech=SpeechStyle(register="casual"),
        )
        now = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)

        result = assemble_prompt(
            character=character,
            mood=MoodState(),
            world=WorldState(),
            short_term_memories=[],
            long_term_memories=[],
            recent_turns=[],
            current_user_message="Hello!",
            now=now,
        )

        assert "Mira" in result.system_prompt
        assert "Hello!" in result.messages[-1]["content"]
        assert result.messages[-1]["role"] == "user"
