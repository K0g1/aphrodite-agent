"""LLM-based memory extraction from conversations."""

from __future__ import annotations

import logging

from ..character import Character
from ..types import Memory, MemoryType, Sensitivity

MEMORY_EXTRACTION_PROMPT = """Analyze this conversation exchange and extract atomic facts to remember about the user.

=== CONVERSATION ===
User: {user_message}
Assistant: {assistant_message}

=== EXTRACT ===
For each fact, output one line in this format:
TYPE|CONTENT|CONFIDENCE|SENSITIVITY|IMPORTANCE

Types: preference, fact, event, boundary, relationship, project, mood, open_loop
Confidence: 0.0-1.0
Sensitivity: low, normal, high
Importance: 0.0-1.0

Only extract facts that are:
- Directly stated or strongly implied
- Useful for future conversations
- About the user (not the assistant)
- True statements (not jokes, hypotheticals, or roleplay)

If nothing worth remembering was said, output: NONE

=== OUTPUT ===
"""


logger = logging.getLogger("aphrodite.extraction")

FALLBACK_EXTRACTIONS: dict[str, tuple[str, float, float]] = {
    "prefer": ("preference", 0.8, 0.6),
    "like": ("preference", 0.7, 0.5),
    "love": ("preference", 0.9, 0.7),
    "hate": ("preference", 0.9, 0.7),
    "don't like": ("preference", 0.9, 0.6),
    "remember": ("fact", 0.7, 0.6),
    "important": ("fact", 0.9, 0.8),
    "always": ("fact", 0.7, 0.6),
    "never": ("fact", 0.7, 0.6),
    "my name": ("fact", 0.95, 0.7),
    "i am": ("fact", 0.6, 0.5),
    "i'm": ("fact", 0.6, 0.5),
    "i live": ("fact", 0.95, 0.7),
    "i work": ("fact", 0.95, 0.8),
    "i study": ("fact", 0.9, 0.7),
    "i have a": ("fact", 0.9, 0.6),
    "i don't have": ("fact", 0.9, 0.6),
    "i can't": ("boundary", 0.7, 0.5),
    "i will": ("open_loop", 0.7, 0.6),
    "i'm going to": ("open_loop", 0.6, 0.5),
    "i need to": ("open_loop", 0.7, 0.6),
    "i should": ("open_loop", 0.5, 0.4),
}


class MemoryExtractor:
    """Extracts memories from conversation exchanges using LLM."""

    def __init__(self, provider=None):
        self._llm = provider

    async def extract(
        self,
        user_msg: str,
        assistant_msg: str,
        character: Character | None = None,
        max_memories: int = 3,
    ) -> list[Memory]:
        """Extract memories from a conversation exchange."""
        if self._llm:
            try:
                return await self._extract_with_llm(user_msg, assistant_msg, max_memories)
            except Exception:
                logger.warning(
                    "LLM memory extraction failed; using keyword fallback", exc_info=True
                )

        return self._extract_with_keywords(user_msg, assistant_msg, max_memories)

    async def _extract_with_llm(
        self, user_msg: str, assistant_msg: str, max_memories: int
    ) -> list[Memory]:
        """Use LLM to extract memories."""
        prompt = MEMORY_EXTRACTION_PROMPT.format(
            user_message=user_msg[:500],
            assistant_message=assistant_msg[:500],
        )

        response = await self._llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.0,
        )

        memories = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line or line == "NONE":
                continue

            parts = line.split("|")
            if len(parts) < 3:
                continue
            mem_type = parts[0].strip().lower()
            content = parts[1].strip()
            try:
                confidence = float(parts[2].strip())
                sensitivity = parts[3].strip().lower() if len(parts) > 3 else "normal"
                importance = float(parts[4].strip()) if len(parts) > 4 else 0.5
            except (ValueError, TypeError):
                # One malformed line must not discard the whole extraction.
                continue

            # Validate
            try:
                mt = MemoryType(mem_type)
            except ValueError:
                mt = MemoryType.FACT

            try:
                sens = Sensitivity(sensitivity)
            except ValueError:
                sens = Sensitivity.NORMAL

            if content and len(content) < 200:
                memories.append(
                    Memory(
                        memory_type=mt,
                        content=content,
                        confidence=min(1.0, max(0.1, confidence)),
                        importance=min(1.0, max(0.1, importance)),
                        sensitivity=sens,
                    )
                )

        return memories[:max_memories]

    def _extract_with_keywords(
        self, user_msg: str, assistant_msg: str, max_memories: int = 3
    ) -> list[Memory]:
        """Fallback: extract memories using keyword matching."""
        memories = []
        lower_msg = user_msg.lower()

        for keyword, (mem_type, confidence, importance) in FALLBACK_EXTRACTIONS.items():
            if keyword in lower_msg:
                # Extract the sentence containing the keyword
                for sentence in user_msg.replace("!", ".").replace("?", ".").split("."):
                    if keyword in sentence.lower():
                        content = sentence.strip()[:150]
                        if content:
                            try:
                                mt = MemoryType(mem_type)
                            except ValueError:
                                mt = MemoryType.FACT
                            memories.append(
                                Memory(
                                    memory_type=mt,
                                    content=content,
                                    confidence=confidence,
                                    importance=importance,
                                )
                            )
                            break

                if len(memories) >= max_memories:
                    break

        # Reject degenerate fragments (e.g. "I am.") that are noise, not facts.
        return [m for m in memories[:max_memories] if len(m.content) >= 10]
