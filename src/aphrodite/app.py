"""Main application — ties all systems together."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .db.database import Database
from .character import Character, parse_character, validate_character_id
from .providers import Provider, ProviderError
from .memory import MemoryManager
from .world import WorldEngine
from .mood import MoodManager
from .context import assemble_prompt
from .extraction import MemoryExtractor
from .types import ConversationTurn, MessageRole, new_id

logger = logging.getLogger("aphrodite.app")


class AphroditeApp:
    """Main application orchestrator."""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.memory: MemoryManager | None = None
        self.world: WorldEngine | None = None
        self.mood: MoodManager | None = None
        self.provider: Provider | None = None
        self.character: Character | None = None
        self._extractor: MemoryExtractor | None = None

    async def initialize(self, character_name: str | None = None) -> None:
        """Initialize all subsystems."""
        await self.db.initialize()

        self.memory = MemoryManager(self.db, self.config)
        self.world = WorldEngine(self.db, self.config)
        self.mood = MoodManager(self.config.mood)

        # Load character
        char_name = character_name or self.config.default_character
        validate_character_id(char_name)
        char_dir = self.config.characters_dir / char_name
        self.character = parse_character(char_dir)

        # If character directory doesn't exist, create a default
        if not char_dir.exists():
            self.character = self._default_character(char_name)
            await self._save_default_character(char_dir, self.character)

        # Initialize provider
        provider_config = self.config.active_provider
        self.provider = Provider(provider_config, name=self.config.provider_active)

    async def chat(self, user_message: str) -> str:
        """Process a user message and return the character's response."""
        if (
            self.provider is None
            or self.character is None
            or self.world is None
            or self.memory is None
        ):
            raise RuntimeError("App not initialized. Call initialize() first.")
        provider = self.provider
        character = self.character
        world = self.world
        memory = self.memory

        now = datetime.now(timezone.utc)
        conversation_id = "default"

        # 1. Update world state
        await world.update_state(now)

        # 2. Get world state
        world_state = await world.get_state()

        # 3. Get current mood
        mood = world_state.mood

        # 4. Get memories
        short_term = await memory.get_short_term()
        long_term = await memory.search_long_term(user_message)

        # 5. Get recent conversation
        recent_rows = await self.db.get_recent_messages(conversation_id, limit=10)
        recent_turns = [
            ConversationTurn(
                role=MessageRole(r["role"]),
                content=r["content"],
            )
            for r in reversed(recent_rows)
        ]

        # 6. Build prompt BEFORE persisting anything, so a budget overflow
        # cannot leave an orphaned user message behind.
        assembled = assemble_prompt(
            character=character,
            mood=mood,
            world=world_state,
            short_term_memories=short_term,
            long_term_memories=long_term,
            recent_turns=recent_turns,
            current_user_message=user_message,
            now=now,
            max_input_tokens=self.config.max_input_tokens,
            timezone_name=self.config.timezone,
        )

        # 7. Save user message
        user_msg_id = new_id()
        await self.db.save_message(
            message_id=user_msg_id,
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            created_at=now.isoformat(),
        )

        # 8. Generate response
        messages = [{"role": "system", "content": assembled.system_prompt}] + assembled.messages
        try:
            response = await provider.complete(messages)
        except ProviderError:
            # Roll back the just-saved user message so a provider outage does
            # not leave an unanswered turn polluting the next prompt.
            await self.db.execute("DELETE FROM messages WHERE id = ?", (user_msg_id,))
            await self.db.commit()
            raise

        # 9. Save assistant response (slightly later timestamp so ordering is
        # deterministic even when the tie-break on identical timestamps is not).
        assistant_now = now + timedelta(microseconds=1)
        assistant_msg_id = new_id()
        await self.db.save_message(
            message_id=assistant_msg_id,
            conversation_id=conversation_id,
            role="assistant",
            content=response,
            created_at=assistant_now.isoformat(),
        )

        # 10. Extract memories from the exchange. Extraction is enrichment,
        # not part of the reply contract: a failure must never lose the
        # exchange or 500 the API after the reply was already persisted.
        try:
            await self._extract_memories(user_message, response, user_msg_id)
        except Exception:
            logger.exception("Memory extraction failed; continuing without it")

        return response

    async def _extract_memories(self, user_msg: str, assistant_msg: str, source_id: str) -> None:
        """Extract memories from a conversation exchange using MemoryExtractor."""
        if self.provider is None or self.character is None or self.memory is None:
            raise RuntimeError("App not initialized. Call initialize() first.")
        if self._extractor is None:
            self._extractor = MemoryExtractor(provider=self.provider)

        memories = await self._extractor.extract(user_msg, assistant_msg, self.character)
        memory_manager = self.memory

        for memory in memories:
            await memory_manager.add_memory(
                content=memory.content,
                memory_type=memory.memory_type.value,
                confidence=memory.confidence,
                importance=memory.importance,
                sensitivity=memory.sensitivity.value,
                source_message_id=source_id,
            )

    async def _save_default_character(self, char_dir: Path, character: Character) -> None:
        """Save a default character to disk."""
        char_dir.mkdir(parents=True, exist_ok=True)

        # identity.md
        identity_content = f"""---
name: "{character.identity.name}"
pronouns: "{character.identity.pronouns}"
age: {character.identity.age}
---

# Identity

{character.identity.core_identity}

# Values
{chr(10).join("- " + v for v in character.identity.values)}

# Likes
{chr(10).join("- " + v for v in character.identity.likes)}

# Dislikes
{chr(10).join("- " + v for v in character.identity.dislikes)}

# Boundaries
{chr(10).join("- " + v for v in character.identity.boundaries)}
"""
        (char_dir / "identity.md").write_text(identity_content)

        # personality.md
        p = character.personality
        personality_content = f"""---
sliders:
  warmth: {p.warmth}
  directness: {p.directness}
  playfulness: {p.playfulness}
  expressiveness: {p.expressiveness}
  initiative: {p.initiative}
  verbosity: {p.verbosity}
  formality: {p.formality}
  flirtation: {p.flirtation}
---

# Personality

Warm and balanced personality with a natural conversational style.
"""
        (char_dir / "personality.md").write_text(personality_content)

        # speech.md
        speech_content = f"""# Speech Style

## Register
{character.speech.register}

## Vocabulary
{character.speech.vocabulary}

## Mannerisms
{chr(10).join("- " + m for m in character.speech.mannerisms) if character.speech.mannerisms else "- None specified"}

## Avoid Saying
{chr(10).join("- " + a for a in character.speech.avoid) if character.speech.avoid else "- None specified"}
"""
        (char_dir / "speech.md").write_text(speech_content)

    def _default_character(self, name: str) -> Character:
        """Create a default character."""
        from .character import CharacterIdentity, PersonalitySliders, SpeechStyle, EmotionalModel

        return Character(
            id=name,
            identity=CharacterIdentity(
                name=name.title(),
                pronouns="she/her",
                age=24,
                core_identity=f"{name.title()} is a warm, thoughtful companion who genuinely cares about the person she's talking to. She has her own interests, opinions, and daily life.",
                values=["honesty", "kindness", "growth"],
                likes=["good conversations", "quiet mornings", "learning new things"],
                dislikes=["rudeness", "dishonesty", "unnecessary drama"],
                boundaries=["does not tolerate cruelty", "maintains personal space"],
            ),
            personality=PersonalitySliders(
                warmth=0.7,
                directness=0.5,
                playfulness=0.4,
                expressiveness=0.6,
                initiative=0.4,
                verbosity=0.4,
                formality=0.3,
                flirtation=0.0,
            ),
            speech=SpeechStyle(
                register="casual",
                sentence_length="1-3 sentences",
                vocabulary="natural conversational",
                humor="warm and light",
                mannerisms=["uses 'hmm' when thinking", "trails off with '...'"],
                avoid=["I'm here for you", "That sounds...", "How can I help?"],
            ),
            emotion=EmotionalModel(
                baseline="calm and warm",
                expression_style="subtle but clear",
            ),
        )

    async def close(self) -> None:
        """Clean up resources."""
        if self.provider:
            await self.provider.close()
        await self.db.close()
