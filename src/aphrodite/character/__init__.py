"""Character file parser — reads markdown character files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CharacterIdentity:
    name: str = "Companion"
    pronouns: str = "they/them"
    age: int = 24
    nature: str = "AI companion character"
    occupation: str = ""
    core_identity: str = ""
    values: list[str] = field(default_factory=list)
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)


@dataclass
class PersonalitySliders:
    warmth: float = 0.6
    directness: float = 0.5
    playfulness: float = 0.4
    expressiveness: float = 0.5
    initiative: float = 0.4
    verbosity: float = 0.4
    formality: float = 0.3
    flirtation: float = 0.0


@dataclass
class SpeechStyle:
    register: str = "casual"
    sentence_length: str = "1-3 sentences"
    vocabulary: str = "natural conversational"
    humor: str = "warm and light"
    pet_names: list[str] = field(default_factory=list)
    mannerisms: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)


@dataclass
class EmotionalModel:
    baseline: str = "calm and warm"
    triggers: list[str] = field(default_factory=list)
    expression_style: str = "subtle but clear"
    recovery_pattern: str = "gradual return to baseline"


@dataclass
class Character:
    """Parsed character from markdown files."""
    id: str = "default"
    identity: CharacterIdentity = field(default_factory=CharacterIdentity)
    personality: PersonalitySliders = field(default_factory=PersonalitySliders)
    speech: SpeechStyle = field(default_factory=SpeechStyle)
    emotion: EmotionalModel = field(default_factory=EmotionalModel)
    background: str = ""
    goals: str = ""
    routines: str = ""
    relationships: str = ""
    first_message: str = ""
    raw_files: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.identity.name


def parse_character(character_dir: Path) -> Character:
    """Parse a character from a directory of markdown files."""
    char = Character()
    char.id = character_dir.name
    
    if not character_dir.exists():
        return char

    # Parse identity.md
    identity_file = character_dir / "identity.md"
    if identity_file.exists():
        content = identity_file.read_text(encoding="utf-8")
        char.raw_files["identity.md"] = content
        char.identity = _parse_identity(content)

    # Parse personality.md
    personality_file = character_dir / "personality.md"
    if personality_file.exists():
        content = personality_file.read_text(encoding="utf-8")
        char.raw_files["personality.md"] = content
        char.personality = _parse_personality(content)

    # Parse speech.md
    speech_file = character_dir / "speech.md"
    if speech_file.exists():
        content = speech_file.read_text(encoding="utf-8")
        char.raw_files["speech.md"] = content
        char.speech = _parse_speech(content)

    # Parse emotional.md
    emotional_file = character_dir / "emotional.md"
    if emotional_file.exists():
        content = emotional_file.read_text(encoding="utf-8")
        char.raw_files["emotional.md"] = content
        char.emotion = _parse_emotional(content)

    # Parse background.md
    bg_file = character_dir / "background.md"
    if bg_file.exists():
        char.background = _strip_frontmatter(bg_file.read_text(encoding="utf-8"))

    # Parse goals.md
    goals_file = character_dir / "goals.md"
    if goals_file.exists():
        char.goals = _strip_frontmatter(goals_file.read_text(encoding="utf-8"))

    return char


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown."""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            return content[end + 4:].lstrip("\n")
    return content


def _extract_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter as dict."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end]
    # Simple YAML parser for flat key-value pairs
    result = {}
    for line in fm_text.strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Try to parse lists
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip('"') for v in value[1:-1].split(",") if v.strip()]
            # Try to parse numbers
            elif value.replace(".", "").isdigit():
                value = float(value) if "." in value else int(value)
            elif value.lower() in ("true", "false"):
                value = value.lower() == "true"
            result[key] = value
    return result


def _parse_identity(content: str) -> CharacterIdentity:
    """Parse identity from markdown."""
    identity = CharacterIdentity()
    
    # Check for YAML frontmatter first
    fm = _extract_frontmatter(content)
    if "name" in fm:
        identity.name = str(fm["name"])
    if "pronouns" in fm:
        identity.pronouns = str(fm["pronouns"])
    if "age" in fm:
        identity.age = int(fm["age"])
    
    # Parse markdown sections
    text = _strip_frontmatter(content)
    
    # Extract sections
    sections = _extract_sections(text)
    
    if "identity" in sections:
        identity.core_identity = sections["identity"].strip()
    if "values" in sections:
        identity.values = _bullet_list(sections["values"])
    if "likes" in sections:
        identity.likes = _bullet_list(sections["likes"])
    if "dislikes" in sections:
        identity.dislikes = _bullet_list(sections["dislikes"])
    if "boundaries" in sections:
        identity.boundaries = _bullet_list(sections["boundaries"])
    
    return identity


def _parse_personality(content: str) -> PersonalitySliders:
    """Parse personality sliders from markdown."""
    sliders = PersonalitySliders()
    
    fm = _extract_frontmatter(content)
    if "sliders" in fm and isinstance(fm["sliders"], dict):
        for key, val in fm["sliders"].items():
            if hasattr(sliders, key):
                setattr(sliders, key, float(val))
    
    return sliders


def _parse_speech(content: str) -> SpeechStyle:
    """Parse speech style from markdown."""
    speech = SpeechStyle()
    text = _strip_frontmatter(content)
    sections = _extract_sections(text)
    
    if "register" in sections:
        speech.register = sections["register"].strip().split("\n")[0]
    if "signature mannerisms" in sections or "mannerisms" in sections:
        key = "signature mannerisms" if "signature mannerisms" in sections else "mannerisms"
        speech.mannerisms = _bullet_list(sections[key])
    if "avoid saying" in sections or "forbidden" in sections:
        key = "avoid saying" if "avoid saying" in sections else "forbidden"
        speech.avoid = _bullet_list(sections[key])
    
    return speech


def _parse_emotional(content: str) -> EmotionalModel:
    """Parse emotional model from markdown."""
    emotion = EmotionalModel()
    text = _strip_frontmatter(content)
    sections = _extract_sections(text)
    
    if "baseline" in sections:
        emotion.baseline = sections["baseline"].strip().split("\n")[0]
    if "triggers" in sections:
        emotion.triggers = _bullet_list(sections["triggers"])
    
    return emotion


def _extract_sections(text: str) -> dict[str, str]:
    """Extract markdown sections by header."""
    sections = {}
    current_header = None
    current_lines = []
    
    for line in text.split("\n"):
        header_match = re.match(r"^#{1,3}\s+(.+)$", line)
        if header_match:
            if current_header:
                sections[current_header.lower()] = "\n".join(current_lines)
            current_header = header_match.group(1)
            current_lines = []
        else:
            current_lines.append(line)
    
    if current_header:
        sections[current_header.lower()] = "\n".join(current_lines)
    
    return sections


def _bullet_list(text: str) -> list[str]:
    """Extract bullet points from text."""
    items = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            items.append(line[2:].strip())
    return items
