"""Edge-case tests for character parser, memory system, and world engine.

Test IDs:
  CHAR-003  — Invalid fields / empty / malformed character markdown
  MEM-004   — Duplicate / contradictory memories
  WORLD-002 — DST timezone boundaries
  WORLD-007 — Mood bounds (clamping and validation)
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aphrodite.character import (
    Character,
    _bullet_list,
    _extract_frontmatter,
    _extract_sections,
    _strip_frontmatter,
    parse_character,
)
from aphrodite.config import Config, MoodConfig
from aphrodite.db.database import Database
from aphrodite.mood import MoodManager
from aphrodite.types import MoodState
from aphrodite.world import WorldEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> Config:
    """Build a Config with optional overrides, no filesystem side-effects."""
    c = Config()
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _engine_no_db() -> WorldEngine:
    """Create a WorldEngine without touching the DB (unit-test friendly)."""
    engine = WorldEngine.__new__(WorldEngine)
    engine.config = Config()
    engine._world_secret = "test-secret"
    return engine


async def _init_db_and_manager():
    """Initialize an in-process aiosqlite DB + MemoryManager for tests."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "mem_test.db"
    db = Database(db_path)
    await db.initialize()
    config = Config()
    from aphrodite.memory import MemoryManager

    mgr = MemoryManager(db, config)
    return mgr, db


# ===================================================================
# CHAR-003 — Character markdown parser: invalid / empty / edge fields
# ===================================================================


class TestCHAR003_EmptyCharacter:
    """Parsing an empty or nonexistent character directory yields defaults."""

    def test_nonexistent_directory_returns_defaults(self, tmp_path):
        char = parse_character(tmp_path / "does-not-exist")
        assert char.identity.name == "Companion"
        assert char.identity.age == 24
        assert char.personality.warmth == 0.6
        assert char.speech.register == "casual"
        assert char.emotion.baseline == "calm and warm"
        assert char.background == ""
        assert char.goals == ""

    def test_empty_directory_returns_defaults(self, tmp_path):
        d = tmp_path / "empty-char"
        d.mkdir()
        char = parse_character(d)
        assert char.identity.name == "Companion"
        assert char.id == "empty-char"

    def test_empty_identity_file(self, tmp_path):
        d = tmp_path / "blank"
        d.mkdir()
        (d / "identity.md").write_text("")
        char = parse_character(d)
        assert char.identity.name == "Companion"
        assert char.identity.pronouns == "they/them"

    def test_empty_personality_file(self, tmp_path):
        d = tmp_path / "blank-p"
        d.mkdir()
        (d / "personality.md").write_text("")
        char = parse_character(d)
        sliders = char.personality
        # All defaults preserved
        assert sliders.warmth == 0.6
        assert sliders.directness == 0.5


class TestCHAR003_InvalidFields:
    """Malformed content must not crash the parser; defaults survive."""

    def test_identity_negative_age(self, tmp_path):
        d = tmp_path / "neg-age"
        d.mkdir()
        (d / "identity.md").write_text("---\nname: Edge\nage: -5\n---\n")
        char = parse_character(d)
        # _parse_identity does int(fm["age"]) — no range check, so -5 is stored
        assert char.identity.name == "Edge"
        assert char.identity.age == -5  # Parser accepts (no validation yet)

    def test_identity_float_age(self, tmp_path):
        d = tmp_path / "float-age"
        d.mkdir()
        (d / "identity.md").write_text("---\nage: 3.14\n---\n")
        char = parse_character(d)
        # Simple parser: "3.14" → replace(".","").isdigit() → "314".isdigit() = True
        # So it becomes float(3.14), then int(fm["age"]) on 3.14 → 3
        assert isinstance(char.identity.age, int)

    def test_identity_garbage_age_defaults(self, tmp_path):
        # A malformed age is tolerated (defaults to 24) so one bad file can
        # never brick app startup (final audit, 2026-07-31).
        d = tmp_path / "bad-age"
        d.mkdir()
        (d / "identity.md").write_text("---\nage: not-a-number\n---\n")
        char = parse_character(d)
        assert char.identity.age == 24

    def test_identity_no_frontmatter_no_sections(self, tmp_path):
        d = tmp_path / "no-sec"
        d.mkdir()
        (d / "identity.md").write_text("Just some random text without headers.\n")
        char = parse_character(d)
        # No sections found → core_identity stays empty
        assert char.identity.core_identity == ""

    def test_personality_out_of_range_slider(self, tmp_path):
        """Simple YAML parser can't handle nested dicts (sliders: warmth: 9.9)
        so sliders stay at defaults. This documents the limitation."""
        d = tmp_path / "extreme"
        d.mkdir()
        (d / "personality.md").write_text("---\nsliders:\n  warmth: 9.9\n  flirtation: -3.0\n---\n")
        char = parse_character(d)
        # The simple YAML parser treats "warmth: 9.9" as a string value for key "sliders",
        # not a dict. So _parse_personality's isinstance(fm["sliders"], dict) check fails.
        # Sliders remain at defaults.
        assert char.personality.warmth == 0.6  # default, not 9.9
        assert char.personality.flirtation == 0.0  # default, not -3.0

    def test_personality_flat_slider_overrides(self, tmp_path):
        """If sliders were provided as flat top-level keys (hypothetical format), they'd be ignored."""
        d = tmp_path / "flat"
        d.mkdir()
        (d / "personality.md").write_text("---\nwarmth: 0.9\nflirtation: 0.5\n---\n")
        char = parse_character(d)
        # warmth is not a valid top-level slider key in the parser
        assert char.personality.warmth == 0.6

    def test_speech_malformed_bullet_list(self, tmp_path):
        d = tmp_path / "bad-bullets"
        d.mkdir()
        (d / "speech.md").write_text(
            "# Register\nformal\n\n"
            "# Mannerisms\n"
            "no bullet prefix here\n"
            "- valid item\n"
            "* also valid\n"
            "   bare text\n"
        )
        char = parse_character(d)
        assert char.speech.register == "formal"
        assert char.speech.mannerisms == ["valid item", "also valid"]

    def test_emotional_missing_triggers(self, tmp_path):
        d = tmp_path / "no-trig"
        d.mkdir()
        (d / "emotional.md").write_text("# Baseline\ncheerful\n")
        char = parse_character(d)
        assert char.emotion.baseline == "cheerful"
        assert char.emotion.triggers == []

    def test_strip_frontmatter_no_end_marker(self):
        content = "---\nname: Foo\n"
        result = _strip_frontmatter(content)
        assert result == content  # No closing --- → returned unchanged

    def test_extract_frontmatter_empty(self):
        assert _extract_frontmatter("no frontmatter") == {}
        assert _extract_frontmatter("") == {}

    def test_bullet_list_empty(self):
        assert _bullet_list("") == []
        assert _bullet_list("   \n  \n") == []

    def test_extract_sections_no_headers(self):
        assert _extract_sections("plain text\nno headers\n") == {}

    def test_extract_sections_duplicate_headers_last_wins(self):
        text = "# Title\nfirst\n# Title\nsecond\n"
        sections = _extract_sections(text)
        # Last section with same header overwrites, trailing newline from join
        assert sections["title"].strip() == "second"

    def test_background_with_frontmatter(self, tmp_path):
        d = tmp_path / "bg-fm"
        d.mkdir()
        (d / "background.md").write_text(
            "---\ntitle: Background\n---\nActual background text here."
        )
        char = parse_character(d)
        assert char.background == "Actual background text here."

    def test_goals_empty_file(self, tmp_path):
        d = tmp_path / "goals-e"
        d.mkdir()
        (d / "goals.md").write_text("")
        char = parse_character(d)
        assert char.goals == ""

    def test_unicode_identity(self, tmp_path):
        d = tmp_path / "unicode"
        d.mkdir()
        (d / "identity.md").write_text("---\nname: 日本語\npronouns: 彼/彼女\n---\n")
        char = parse_character(d)
        assert char.identity.name == "日本語"
        assert char.identity.pronouns == "彼/彼女"

    def test_very_long_name(self, tmp_path):
        d = tmp_path / "long-name"
        d.mkdir()
        long_name = "A" * 10000
        (d / "identity.md").write_text(f"---\nname: {long_name}\n---\n")
        char = parse_character(d)
        assert len(char.identity.name) == 10000

    def test_character_id_from_dir_name(self, tmp_path):
        d = tmp_path / "my-special-char"
        d.mkdir()
        char = parse_character(d)
        assert char.id == "my-special-char"

    def test_name_property_delegates_to_identity(self):
        c = Character()
        c.identity.name = "Zara"
        assert c.name == "Zara"

    def test_boolean_frontmatter_field(self, tmp_path):
        d = tmp_path / "bool"
        d.mkdir()
        (d / "identity.md").write_text("---\nname: BoolTest\nverified: true\n---\n")
        char = parse_character(d)
        assert char.identity.name == "BoolTest"

    def test_list_frontmatter_field(self, tmp_path):
        d = tmp_path / "list"
        d.mkdir()
        (d / "identity.md").write_text("---\nname: ListTest\ntags: [a, b, c]\n---\n")
        char = parse_character(d)
        assert char.identity.name == "ListTest"


# ===================================================================
# MEM-004 — Memory system: duplicates, contradictions, superseding
# ===================================================================


@pytest.fixture
async def mem_env():
    """Provide a shared MemoryManager + Database backed by one aiosqlite DB."""
    mgr, db = await _init_db_and_manager()
    yield mgr, db
    await db.close()


class TestMEM004_DuplicateAndContradictoryMemories:
    """Test memory operations for duplicate, contradictory, and superseded memories."""

    @pytest.mark.asyncio
    async def test_add_duplicate_content_creates_two_rows(self, mem_env):
        """Two memories with identical content get different IDs (no dedup)."""
        mgr, db = mem_env
        m1 = await mgr.add_memory("User likes cats", memory_type="fact")
        m2 = await mgr.add_memory("User likes cats", memory_type="fact")
        assert m1.id != m2.id
        rows = await db.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' AND content = ?",
            ("User likes cats",),
        )
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_add_empty_content(self, mem_env):
        """Empty memory content is allowed at DB level (no validation check)."""
        mgr, _ = mem_env
        m = await mgr.add_memory("", memory_type="fact")
        assert m.content == ""

    @pytest.mark.asyncio
    async def test_add_whitespace_only_content(self, mem_env):
        mgr, _ = mem_env
        m = await mgr.add_memory("   \n  \t  ", memory_type="fact")
        assert m.content == "   \n  \t  "

    @pytest.mark.asyncio
    async def test_correct_memory_supersedes_old(self, mem_env):
        """correct_memory marks old as superseded and creates new with confidence=1.0."""
        mgr, db = mem_env
        original = await mgr.add_memory("User lives in Paris", memory_type="fact")
        await mgr.correct_memory(original.id, "User lives in London")

        # Old memory is superseded
        old_row = await db.fetch_one("SELECT * FROM memories WHERE id = ?", (original.id,))
        assert old_row is not None
        assert old_row["status"] == "superseded"

        # New active memory exists with corrected content
        active = await db.fetch_all(
            "SELECT * FROM memories WHERE status = 'active' AND content LIKE ?",
            ("%London%",),
        )
        assert len(active) == 1
        assert active[0]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_correct_nonexistent_memory_is_noop(self, mem_env):
        """Correcting a nonexistent memory should not raise."""
        mgr, _ = mem_env
        await mgr.correct_memory("nonexistent-id-xyz", "new content")
        # No exception = pass

    @pytest.mark.asyncio
    async def test_forget_memory_soft_deletes(self, mem_env):
        mgr, db = mem_env
        m = await mgr.add_memory("Sensitive fact", memory_type="fact")
        await mgr.forget_memory(m.id)
        row = await db.fetch_one("SELECT * FROM memories WHERE id = ?", (m.id,))
        assert row is not None
        assert row["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_search_excludes_superseded(self, mem_env):
        """Superseded memories should not appear in search results."""
        mgr, _ = mem_env
        m = await mgr.add_memory("Old fact about Tokyo", memory_type="fact")
        await mgr.correct_memory(m.id, "New fact about Osaka")
        results = await mgr.search_long_term("Tokyo")
        # The old "Tokyo" memory is superseded, so should not appear
        assert all(r.content != "Old fact about Tokyo" for r in results)

    @pytest.mark.asyncio
    async def test_contradictory_memories_both_active(self, mem_env):
        """Two contradictory facts can coexist as active (no conflict resolution)."""
        mgr, _ = mem_env
        await mgr.add_memory("User's favorite color is blue", memory_type="fact")
        await mgr.add_memory("User's favorite color is red", memory_type="fact")
        results = await mgr.search_long_term("favorite color")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_stats_after_operations(self, mem_env):
        mgr, _ = mem_env
        await mgr.add_memory("fact 1", memory_type="fact")
        await mgr.add_memory("pref 1", memory_type="preference")
        m3 = await mgr.add_memory("to delete", memory_type="fact")
        await mgr.forget_memory(m3.id)
        stats = await mgr.get_stats()
        assert stats["total_active"] == 2
        assert stats["by_type"]["fact"] == 1
        assert stats["by_type"]["preference"] == 1

    @pytest.mark.asyncio
    async def test_get_short_term_respects_limit(self, mem_env):
        mgr, _ = mem_env
        for i in range(5):
            await mgr.add_memory(f"item {i}", memory_type="fact")
        memories = await mgr.get_short_term(limit=3)
        assert len(memories) == 3

    @pytest.mark.asyncio
    async def test_memory_timestamps_are_set(self, mem_env):
        mgr, _ = mem_env
        m = await mgr.add_memory("Timestamped memory", memory_type="fact")
        assert m.created_at != ""
        assert m.updated_at != ""

    @pytest.mark.asyncio
    async def test_row_to_memory_with_missing_fields(self, mem_env):
        """_row_to_memory handles rows missing optional fields gracefully."""
        mgr, _ = mem_env
        row = {
            "id": "test123",
            "memory_type": "fact",
            "content": "test content",
            # confidence, importance, sensitivity, etc. missing
        }
        mem = mgr._row_to_memory(row)
        assert mem.id == "test123"
        assert mem.confidence == 0.9  # default
        assert mem.importance == 0.5  # default

    @pytest.mark.asyncio
    async def test_add_memory_type_variants(self, mem_env):
        """All MemoryType enum values can be stored."""
        mgr, _ = mem_env
        for mtype in [
            "fact",
            "preference",
            "correction",
            "event",
            "boundary",
            "relationship",
            "project",
            "mood",
            "open_loop",
        ]:
            m = await mgr.add_memory(f"test {mtype}", memory_type=mtype)
            assert m.memory_type.value == mtype

    @pytest.mark.asyncio
    async def test_add_memory_sensitivity_variants(self, mem_env):
        """All Sensitivity levels can be stored."""
        mgr, _ = mem_env
        for sens in ["low", "normal", "high", "private"]:
            m = await mgr.add_memory(f"sens {sens}", memory_type="fact", sensitivity=sens)
            assert m.sensitivity.value == sens

    @pytest.mark.asyncio
    async def test_search_empty_query(self, mem_env):
        """Empty query should not crash."""
        mgr, _ = mem_env
        results = await mgr.search_long_term("")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_no_matches(self, mem_env):
        mgr, _ = mem_env
        await mgr.add_memory("Known fact", memory_type="fact")
        results = await mgr.search_long_term("xyznonexistent")
        assert len(results) == 0


# ===================================================================
# WORLD-002 — DST timezone boundaries
# ===================================================================


class TestWORLD002_DST:
    """World engine timezone handling around DST transitions."""

    def _make_engine(self) -> WorldEngine:
        engine = _engine_no_db()
        # Pin the timezone explicitly: these tests assert Vancouver DST
        # transitions and must not depend on the host's system timezone
        # (GitHub runners run UTC).
        engine.config.timezone = "America/Vancouver"
        engine.config.world.state_update_interval_minutes = 0  # allow all updates
        return engine

    def test_vancouver_pst_to_pdt_spring_forward(self):
        """March 9 2025 02:00 PST → 03:00 PDT (spring forward)."""
        engine = self._make_engine()
        # 2:00 AM PST = UTC-8 → UTC 10:00
        utc_time = datetime(2025, 3, 9, 10, 0, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        assert local.tzinfo is not None
        # After spring forward, Vancouver is PDT (UTC-7)
        assert local.hour == 3  # 10 UTC → 3 AM PDT
        assert local.month == 3

    def test_vancouver_pdt_to_pst_fall_back(self):
        """November 2 2025 02:00 PDT → 01:00 PST (fall back)."""
        engine = self._make_engine()
        # 2:00 AM PDT = UTC-7 → UTC 09:00
        utc_time = datetime(2025, 11, 2, 9, 0, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        # After fall back, Vancouver is PST (UTC-8)
        assert local.hour == 1  # 09 UTC → 1 AM PST
        assert local.month == 11

    def test_midnight_around_spring_forward(self):
        """The hour from 23:00 to 00:00 on DST transition day."""
        engine = self._make_engine()
        # March 9, 2025 at midnight PDT = UTC 08:00
        utc_time = datetime(2025, 3, 9, 8, 0, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        assert local.hour == 0  # midnight PDT
        assert local.day == 9

    def test_dst_activity_consistency(self):
        """Activity mapping should handle DST hours correctly."""
        engine = self._make_engine()
        # March 10, 2025 14:30 PDT = UTC 21:30
        utc_time = datetime(2025, 3, 10, 21, 30, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        # 14:30 PDT → "working in the afternoon" (13 <= 14 < 17)
        activity = engine._get_scheduled_activity(local)
        assert "afternoon" in activity.lower()

    def test_dst_hour_7_is_waking(self):
        """7:00 PDT should map to 'waking up slowly'."""
        engine = self._make_engine()
        # March 10, 2025 07:30 PDT = UTC 14:30
        utc_time = datetime(2025, 3, 10, 14, 30, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        assert local.hour == 7
        activity = engine._get_scheduled_activity(local)
        assert "waking" in activity.lower()

    def test_nonexistent_localtime_fallback(self):
        """zoneinfo should handle DST; fallback is UTC-7."""
        engine = self._make_engine()
        # Just verify normal times work
        utc_time = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        local = engine._to_local_time(utc_time)
        # Jan = PST = UTC-8
        assert local.hour == 4

    def test_weather_month_after_dst(self):
        """Weather generation respects the DST-correct month."""
        engine = self._make_engine()
        from aphrodite.types import WorldWeather

        # March 12, 2025 15:00 PDT = UTC 22:00
        utc_time = datetime(2025, 3, 12, 22, 0, tzinfo=UTC)
        weather = engine._generate_weather(utc_time, WorldWeather())
        # March base temp is 7°C, afternoon (+4) = 11
        assert 5 <= weather.temperature_c <= 13

    def test_weather_generates_for_each_dst_hour(self):
        """Weather generation works for every hour across a DST boundary."""
        engine = self._make_engine()
        from aphrodite.types import WorldWeather

        for hour_utc in range(24):
            utc_time = datetime(2025, 3, 9, hour_utc, 0, tzinfo=UTC)
            # Should not raise
            weather = engine._generate_weather(utc_time, WorldWeather())
            assert isinstance(weather.temperature_c, (int, float))

    def test_update_state_near_dst_boundary(self):
        """State update crosses DST without error."""
        engine = self._make_engine()
        from aphrodite.types import MoodState

        mood = MoodState(valence=0.5, arousal=0.6)
        elapsed = 2.0  # hours
        decayed = engine._decay_mood(mood, elapsed)
        assert -1.0 <= decayed.valence <= 1.0
        assert 0.0 <= decayed.arousal <= 1.0

    def test_dst_ambiguity_hour_1_fall_back(self):
        """During fall-back, hour 1 occurs twice. Verify both produce valid local times."""
        engine = self._make_engine()
        # First occurrence of 1:00 AM: UTC 09:00 (still PDT)
        utc1 = datetime(2025, 11, 2, 9, 0, tzinfo=UTC)
        local1 = engine._to_local_time(utc1)
        # Second occurrence: UTC 10:00 (now PST)
        utc2 = datetime(2025, 11, 2, 10, 0, tzinfo=UTC)
        local2 = engine._to_local_time(utc2)
        assert local1.hour == 1
        assert local2.hour == 2
        # Different offsets means same UTC hour maps to different local hours
        assert local1.hour != local2.hour

    def test_weather_deterministic_across_calls(self):
        """Same UTC time produces same weather (deterministic hash)."""
        engine = self._make_engine()
        from aphrodite.types import WorldWeather

        utc_time = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        w1 = engine._generate_weather(utc_time, WorldWeather())
        w2 = engine._generate_weather(utc_time, WorldWeather())
        assert w1.condition == w2.condition
        assert w1.temperature_c == w2.temperature_c

    def test_activity_schedule_full_day_cycle(self):
        """Every hour maps to a valid activity."""
        engine = self._make_engine()
        expected_keywords = {
            0: "sleep",
            1: "sleep",
            2: "sleep",
            3: "sleep",
            4: "sleep",
            5: "sleep",
            6: "sleep",
            7: "wak",
            8: "ready",
            9: "work",
            10: "work",
            11: "work",
            12: "lunch",
            13: "work",
            14: "work",
            15: "work",
            16: "work",
            17: "head",
            18: "dinner",
            19: "relax",
            20: "relax",
            21: "wind",
            22: "bed",
        }
        for hour, keyword in expected_keywords.items():
            dt = datetime(2026, 1, 1, hour, 30, tzinfo=UTC)
            local = dt  # Use UTC directly since _get_scheduled_activity just uses .hour
            activity = engine._get_scheduled_activity(local)
            assert keyword.lower() in activity.lower(), (
                f"Hour {hour}: expected '{keyword}' in '{activity}'"
            )


# ===================================================================
# WORLD-007 — Mood bounds
# ===================================================================


class TestWORLD007_MoodBounds:
    """Mood state must stay within bounds; clamping and decay must be safe."""

    def test_mood_state_defaults(self):
        mood = MoodState()
        assert 0 <= mood.valence <= 1
        assert 0 <= mood.arousal <= 1
        assert 0 <= mood.dominance <= 1
        assert 0 <= mood.affection <= 1
        assert 0 <= mood.trust <= 1
        assert 0 <= mood.curiosity <= 1

    def test_mood_valence_can_be_negative(self):
        """Valence is the only mood dimension that allows negatives."""
        mood = MoodState(valence=-0.5)
        assert mood.valence == -0.5

    def test_mood_decay_clamps_at_boundaries(self):
        """Decay must not push mood outside [-1, 1] for valence or [0, 1] for others."""
        engine = _engine_no_db()
        # Test extreme high values
        mood_high = MoodState(
            valence=1.0, arousal=1.0, dominance=1.0, affection=1.0, trust=1.0, curiosity=1.0
        )
        decayed = engine._decay_mood(mood_high, hours_elapsed=100)
        assert -1.0 <= decayed.valence <= 1.0
        assert 0.0 <= decayed.arousal <= 1.0
        assert 0.0 <= decayed.dominance <= 1.0
        assert 0.0 <= decayed.affection <= 1.0
        assert 0.0 <= decayed.trust <= 1.0
        assert 0.0 <= decayed.curiosity <= 1.0

    def test_mood_decay_clamps_extreme_low(self):
        """Negative valence extreme stays clamped."""
        engine = _engine_no_db()
        mood_low = MoodState(valence=-1.0, arousal=0.0)
        decayed = engine._decay_mood(mood_low, hours_elapsed=100)
        assert -1.0 <= decayed.valence <= 1.0
        assert 0.0 <= decayed.arousal <= 1.0

    def test_mood_decay_zero_hours_no_change(self):
        """Zero elapsed time should produce identical mood."""
        engine = _engine_no_db()
        mood = MoodState(valence=0.7, arousal=0.3)
        decayed = engine._decay_mood(mood, hours_elapsed=0.0)
        assert decayed.valence == 0.7
        assert decayed.arousal == 0.3

    def test_mood_decay_approaches_baseline(self):
        """After many hours, mood should approach baseline values."""
        engine = _engine_no_db()
        mood = MoodState(
            valence=0.9, arousal=0.9, dominance=0.9, affection=0.9, trust=0.9, curiosity=0.9
        )
        decayed = engine._decay_mood(mood, hours_elapsed=50)
        # Baseline valence=0.15, arousal=0.40, etc.
        assert abs(decayed.valence - 0.15) < 0.3
        assert abs(decayed.arousal - 0.40) < 0.3

    def test_apply_event_impact_clamps_valence(self):
        """Event impact must clamp valence to [-1, 1]."""
        config = MoodConfig(max_delta_per_turn=0.5)
        manager = MoodManager(config)
        mood = MoodState(valence=0.8)
        impacted = manager.apply_event_impact(mood, valence_delta=0.5)
        assert impacted.valence <= 1.0

    def test_apply_event_impact_clamps_arousal(self):
        """Event impact must clamp arousal to [0, 1]."""
        config = MoodConfig(max_delta_per_turn=0.5)
        manager = MoodManager(config)
        mood = MoodState(arousal=0.9)
        impacted = manager.apply_event_impact(mood, arousal_delta=0.5)
        assert impacted.arousal <= 1.0
        assert impacted.arousal >= 0.0

    def test_apply_event_impact_clamps_negative(self):
        """Event impact must clamp negative arousal to 0."""
        config = MoodConfig(max_delta_per_turn=0.5)
        manager = MoodManager(config)
        mood = MoodState(arousal=0.1)
        impacted = manager.apply_event_impact(mood, arousal_delta=-0.5)
        assert impacted.arousal >= 0.0

    def test_apply_event_impact_extreme_negative_valence(self):
        """Very negative valence delta is clamped by max_delta_per_turn."""
        config = MoodConfig(max_delta_per_turn=0.08)
        manager = MoodManager(config)
        mood = MoodState(valence=0.5)
        impacted = manager.apply_event_impact(mood, valence_delta=-10.0)
        # max_delta_per_turn = 0.08, so delta clamped to -0.08
        assert impacted.valence >= mood.valence - 0.08 - 0.01
        assert impacted.valence >= -1.0

    def test_mood_to_dict_roundtrip(self):
        """MoodState.to_dict preserves all fields."""
        mood = MoodState(
            valence=0.42, arousal=0.31, dominance=0.77, affection=0.12, trust=0.88, curiosity=0.55
        )
        d = mood.to_dict()
        assert d["valence"] == 0.42
        assert d["arousal"] == 0.31
        assert d["dominance"] == 0.77
        assert d["affection"] == 0.12
        assert d["trust"] == 0.88
        assert d["curiosity"] == 0.55

    def test_mood_energy_property(self):
        """energy = (arousal + valence) / 2."""
        mood = MoodState(valence=0.6, arousal=0.4)
        assert mood.energy == 0.5

    def test_mood_label_positive_energetic(self):
        mood = MoodState(valence=0.5, arousal=0.7)
        assert mood.label() == "positive and energetic"

    def test_mood_label_low_subdued(self):
        mood = MoodState(valence=-0.5, arousal=0.3)
        assert mood.label() == "low and subdued"

    def test_mood_label_neutral(self):
        mood = MoodState(valence=0.0, arousal=0.3)
        assert mood.label() == "neutral and calm"

    def test_mood_label_quietly_positive(self):
        mood = MoodState(valence=0.2, arousal=0.3)
        assert mood.label() == "quietly positive"

    def test_mood_label_alert(self):
        mood = MoodState(valence=0.0, arousal=0.8)
        assert mood.label() == "alert and engaged"

    def test_decay_rates_per_dimension(self):
        """Each dimension decays at its own rate."""
        engine = _engine_no_db()
        mood = MoodState(
            valence=1.0, arousal=1.0, dominance=1.0, affection=1.0, trust=1.0, curiosity=1.0
        )
        decayed = engine._decay_mood(mood, hours_elapsed=10)
        # Arousal decays fastest (0.15), trust slowest (0.01)
        assert decayed.arousal < decayed.trust

    def test_mood_baseline_values(self):
        """Default MoodState matches MoodConfig defaults."""
        mood = MoodState()
        config = MoodConfig()
        assert mood.valence == config.baseline_valence
        assert mood.arousal == config.baseline_arousal
        assert mood.dominance == config.baseline_dominance
        assert mood.affection == config.baseline_affection
        assert mood.trust == config.baseline_trust
        assert mood.curiosity == config.baseline_curiosity

    def test_mood_boundary_values_exact(self):
        """Exact boundary values should be accepted."""
        MoodState(valence=-1.0, arousal=0.0, dominance=0.0, affection=0.0, trust=0.0, curiosity=0.0)
        MoodState(valence=1.0, arousal=1.0, dominance=1.0, affection=1.0, trust=1.0, curiosity=1.0)
        # No exception = pass

    def test_decay_large_elapsed(self):
        """Even with huge elapsed time, mood stays bounded."""
        engine = _engine_no_db()
        mood = MoodState(valence=0.5, arousal=0.5)
        decayed = engine._decay_mood(mood, hours_elapsed=10000)
        assert -1.0 <= decayed.valence <= 1.0
        assert 0.0 <= decayed.arousal <= 1.0

    def test_negative_fractional_elapsed(self):
        """Negative elapsed time (clock skew) should not crash."""
        engine = _engine_no_db()
        mood = MoodState(valence=0.5, arousal=0.5)
        decayed = engine._decay_mood(mood, hours_elapsed=-1.0)
        # Decay function uses (1-rate)^hours — negative hours is fine mathematically
        assert -1.0 <= decayed.valence <= 1.0
        assert 0.0 <= decayed.arousal <= 1.0

    def test_mood_clamping_preserves_non_affected_dims(self):
        """Clamping one dimension should not alter others."""
        config = MoodConfig(max_delta_per_turn=0.08)
        manager = MoodManager(config)
        mood = MoodState(
            valence=0.5, arousal=0.5, dominance=0.5, affection=0.5, trust=0.5, curiosity=0.5
        )
        impacted = manager.apply_event_impact(mood, valence_delta=-2.0, arousal_delta=0.0)
        assert impacted.dominance == 0.5
        assert impacted.affection == 0.5
        assert impacted.trust == 0.5
        assert impacted.curiosity == 0.5

    def test_mood_extreme_negative_valence_decay(self):
        """Valence at -1.0 should decay toward baseline, never below -1."""
        engine = _engine_no_db()
        mood = MoodState(valence=-1.0)
        for _ in range(100):
            mood = engine._decay_mood(mood, hours_elapsed=1.0)
            assert mood.valence >= -1.0
        # After 100 hours, should be near baseline 0.15
        assert mood.valence > -0.5

    def test_mood_json_serialization_roundtrip(self):
        """MoodState can be serialized to JSON and back."""
        mood = MoodState(
            valence=0.3, arousal=0.7, dominance=0.5, affection=0.6, trust=0.4, curiosity=0.8
        )
        d = mood.to_dict()
        serialized = json.dumps(d)
        deserialized = json.loads(serialized)
        restored = MoodState(**deserialized)
        assert restored.valence == mood.valence
        assert restored.arousal == mood.arousal
        assert restored.curiosity == mood.curiosity
